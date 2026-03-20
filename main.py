import calendar
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine, get_db
from hyundai_client import HyundaiClient
from models import DailyManualEntry, DailyReport, OdometerLog, TokenStore, VehicleStore
from report_builder import generate_monthly_report_pdf, generate_monthly_report_xlsx
from scheduler import (
    _collect_odometer,
    _finalize_daily_report,
    scheduler_status,
    start_scheduler,
    stop_scheduler,
)

load_dotenv()

app = FastAPI(title="Hyundai Vehicle Log API", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent


class ManualEntryUpsert(BaseModel):
    passenger_name: str | None = Field(default=None)
    start_time: str | None = Field(default=None, description="HH:MM")
    end_time: str | None = Field(default=None, description="HH:MM")
    odometer_start: int | None = Field(default=None)
    odometer_end: int | None = Field(default=None)
    distance_km: int | None = Field(default=None)


class StartSchedulerRequest(BaseModel):
    car_id: str | None = None


def _first_vehicle_id(db: Session) -> str | None:
    vehicle = db.query(VehicleStore).first()
    return vehicle.car_id if vehicle else None


async def _sync_vehicles_from_api(db: Session, client: HyundaiClient, access_token: str) -> list[VehicleStore]:
    result = await client.get_vehicle_list(access_token)
    payload = result.get("json") or {}
    cars = payload.get("cars", [])

    synced_rows = []
    for car in cars:
        car_id = car.get("carId")
        if not car_id:
            continue

        row = db.query(VehicleStore).filter(VehicleStore.car_id == car_id).first()
        if row is None:
            row = VehicleStore(car_id=car_id)
            db.add(row)

        row.car_name = car.get("carName")
        row.car_nickname = car.get("carNickName")
        row.car_sellname = car.get("carSellName")
        row.car_type = car.get("carType")
        synced_rows.append(row)

    db.commit()
    return synced_rows


async def _bootstrap_automation() -> dict:
    db: Session = SessionLocal()
    client = HyundaiClient()

    try:
        access_token = await _ensure_valid_access_token(db, client)
        if not access_token:
            return {"token": False, "vehicles": 0, "scheduler_started": False}

        synced_rows = await _sync_vehicles_from_api(db, client, access_token)
        car_id = _first_vehicle_id(db)
        if car_id:
            start_scheduler(car_id)

        return {
            "token": True,
            "vehicles": len(synced_rows),
            "scheduler_started": bool(car_id),
            "car_id": car_id,
        }
    finally:
        db.close()


async def _ensure_valid_access_token(db: Session, client: HyundaiClient) -> str | None:
    token_row = db.query(TokenStore).first()
    if not token_row:
        return None

    now = datetime.utcnow()
    if token_row.access_token and token_row.expires_at and token_row.expires_at > now:
        return token_row.access_token

    if not token_row.refresh_token:
        return None

    refresh_result = await client.refresh_access_token(token_row.refresh_token)
    payload = refresh_result.get("json") or {}

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = int(payload.get("expires_in", 3600))

    if not access_token:
        return None

    token_row.access_token = access_token
    if refresh_token:
        token_row.refresh_token = refresh_token
    token_row.expires_at = datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 60))
    token_row.updated_at = datetime.utcnow()
    db.commit()

    return token_row.access_token


@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    try:
        result = await _bootstrap_automation()
        print(f"[STARTUP] bootstrap={result}")
    except Exception as exc:
        print(f"[STARTUP] bootstrap failed: {exc}")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    dashboard_path = BASE_DIR / "dashboard.html"
    return dashboard_path.read_text(encoding="utf-8")


@app.get("/auth/login-url")
def auth_login_url():
    client = HyundaiClient()
    return {"login_url": client.get_login_url()}


@app.get("/auth/callback")
async def auth_callback(code: str, db: Session = Depends(get_db)):
    client = HyundaiClient()
    result = await client.exchange_code(code)
    payload = result.get("json") or {}

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = int(payload.get("expires_in", 3600))

    if not access_token:
        raise HTTPException(status_code=400, detail={"token_exchange_failed": result})

    token_row = db.query(TokenStore).first()
    if token_row is None:
        token_row = TokenStore()
        db.add(token_row)

    token_row.access_token = access_token
    token_row.refresh_token = refresh_token
    token_row.expires_at = datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 60))
    token_row.updated_at = datetime.utcnow()
    db.commit()

    synced_rows = await _sync_vehicles_from_api(db, client, access_token)
    car_id = _first_vehicle_id(db)
    if car_id:
        start_scheduler(car_id)

    return {
        "saved": True,
        "vehicle_count": len(synced_rows),
        "scheduler_started": bool(car_id),
        "car_id": car_id,
    }


@app.post("/vehicles/sync")
async def sync_vehicles(db: Session = Depends(get_db)):
    client = HyundaiClient()
    access_token = await _ensure_valid_access_token(db, client)
    if not access_token:
        raise HTTPException(status_code=400, detail="No valid token. Complete OAuth first.")

    synced_rows = await _sync_vehicles_from_api(db, client, access_token)
    return {
        "count": len(synced_rows),
        "vehicles": [
            {
                "car_id": row.car_id,
                "car_name": row.car_name,
                "car_sellname": row.car_sellname,
                "car_type": row.car_type,
            }
            for row in synced_rows
        ],
    }


@app.get("/vehicles")
def list_vehicles(db: Session = Depends(get_db)):
    rows = db.query(VehicleStore).all()
    return {
        "vehicles": [
            {
                "car_id": r.car_id,
                "car_name": r.car_name,
                "car_sellname": r.car_sellname,
                "car_type": r.car_type,
            }
            for r in rows
        ]
    }


@app.get("/dashboard/data")
def dashboard_data(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    car_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    today = date.today()
    resolved_year = year or today.year
    resolved_month = month or today.month

    if resolved_month < 1 or resolved_month > 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")

    resolved_car_id = car_id or _first_vehicle_id(db)
    vehicles = db.query(VehicleStore).order_by(VehicleStore.created_at.asc()).all()

    odometer_total = db.query(OdometerLog).count()
    daily_total = db.query(DailyReport).count()
    manual_total = db.query(DailyManualEntry).count()

    recent_reports_query = db.query(DailyReport).order_by(DailyReport.drive_date.desc()).limit(10)
    recent_logs_query = (
        db.query(OdometerLog)
        .order_by(OdometerLog.log_date.desc(), OdometerLog.log_time.desc())
        .limit(12)
    )

    recent_reports = [
        {
            "car_id": row.car_id,
            "drive_date": str(row.drive_date),
            "start_time": row.start_time,
            "end_time": row.end_time,
            "odometer_start": row.odometer_start,
            "odometer_end": row.odometer_end,
            "distance_km": row.distance_km,
        }
        for row in recent_reports_query.all()
    ]
    recent_logs = [
        {
            "car_id": row.car_id,
            "log_date": str(row.log_date),
            "log_time": row.log_time,
            "odometer_value": row.odometer_value,
        }
        for row in recent_logs_query.all()
    ]

    monthly = {
        "year": resolved_year,
        "month": resolved_month,
        "car_id": resolved_car_id,
        "daily_report_count": 0,
        "manual_entry_count": 0,
        "total_distance_km": 0,
    }

    if resolved_car_id:
        _, last_day = calendar.monthrange(resolved_year, resolved_month)
        start_of_month = date(resolved_year, resolved_month, 1)
        end_of_month = date(resolved_year, resolved_month, last_day)

        reports = (
            db.query(DailyReport)
            .filter(
                DailyReport.car_id == resolved_car_id,
                DailyReport.drive_date >= start_of_month,
                DailyReport.drive_date <= end_of_month,
            )
            .order_by(DailyReport.drive_date.asc())
            .all()
        )
        manuals = (
            db.query(DailyManualEntry)
            .filter(
                DailyManualEntry.car_id == resolved_car_id,
                DailyManualEntry.drive_date >= start_of_month,
                DailyManualEntry.drive_date <= end_of_month,
            )
            .all()
        )

        monthly["daily_report_count"] = len(reports)
        monthly["manual_entry_count"] = len(manuals)
        monthly["total_distance_km"] = sum((row.distance_km or 0) for row in reports)

    scheduler = scheduler_status()

    return {
        "summary": {
            "vehicle_count": len(vehicles),
            "odometer_log_count": odometer_total,
            "daily_report_count": daily_total,
            "manual_entry_count": manual_total,
            "has_token": db.query(TokenStore).first() is not None,
        },
        "scheduler": scheduler,
        "vehicles": [
            {
                "car_id": row.car_id,
                "car_name": row.car_name,
                "car_sellname": row.car_sellname,
                "car_type": row.car_type,
            }
            for row in vehicles
        ],
        "monthly": monthly,
        "recent_reports": recent_reports,
        "recent_logs": recent_logs,
    }


@app.post("/scheduler/start")
def scheduler_start(req: StartSchedulerRequest, db: Session = Depends(get_db)):
    car_id = req.car_id or _first_vehicle_id(db)
    if not car_id:
        raise HTTPException(status_code=400, detail="No vehicle found. Run /vehicles/sync first.")

    start_scheduler(car_id)
    return {"started": True, "car_id": car_id}


@app.post("/scheduler/stop")
def scheduler_stop():
    stop_scheduler()
    return {"stopped": True}


@app.get("/scheduler/status")
def scheduler_state():
    return scheduler_status()


@app.post("/scheduler/collect-now")
async def collect_now(
    car_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    resolved_car_id = car_id or _first_vehicle_id(db)
    if not resolved_car_id:
        raise HTTPException(status_code=400, detail="No vehicle found.")

    await _collect_odometer(resolved_car_id, force=True)

    latest_log = (
        db.query(OdometerLog)
        .filter(OdometerLog.car_id == resolved_car_id)
        .order_by(OdometerLog.log_date.desc(), OdometerLog.log_time.desc())
        .first()
    )

    return {
        "collected": True,
        "car_id": resolved_car_id,
        "latest_log": (
            {
                "log_date": str(latest_log.log_date),
                "log_time": latest_log.log_time,
                "odometer_value": latest_log.odometer_value,
                "api_timestamp": latest_log.api_timestamp,
            }
            if latest_log
            else None
        ),
    }


@app.post("/reports/finalize")
def finalize_one_day(
    car_id: str | None = Query(default=None),
    target_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    resolved_car_id = car_id or _first_vehicle_id(db)
    if not resolved_car_id:
        raise HTTPException(status_code=400, detail="No vehicle found.")

    resolved_date = target_date or date.today()
    _finalize_daily_report(resolved_car_id, resolved_date)

    return {"finalized": True, "car_id": resolved_car_id, "date": str(resolved_date)}


@app.put("/manual-entries/{car_id}/{drive_date}")
def upsert_manual_entry(
    car_id: str,
    drive_date: date,
    req: ManualEntryUpsert,
    db: Session = Depends(get_db),
):
    row = (
        db.query(DailyManualEntry)
        .filter(
            DailyManualEntry.car_id == car_id,
            DailyManualEntry.drive_date == drive_date,
        )
        .first()
    )

    if row is None:
        row = DailyManualEntry(car_id=car_id, drive_date=drive_date)
        db.add(row)

    row.passenger_name = req.passenger_name
    row.start_time = req.start_time
    row.end_time = req.end_time
    row.odometer_start = req.odometer_start
    row.odometer_end = req.odometer_end
    row.distance_km = req.distance_km
    row.updated_at = datetime.utcnow()

    db.commit()
    return {"saved": True}


@app.get("/manual-entries/{car_id}/{drive_date}")
def get_manual_entry(car_id: str, drive_date: date, db: Session = Depends(get_db)):
    row = (
        db.query(DailyManualEntry)
        .filter(
            DailyManualEntry.car_id == car_id,
            DailyManualEntry.drive_date == drive_date,
        )
        .first()
    )

    if row is None:
        return {"exists": False}

    return {
        "exists": True,
        "data": {
            "passenger_name": row.passenger_name,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "odometer_start": row.odometer_start,
            "odometer_end": row.odometer_end,
            "distance_km": row.distance_km,
        },
    }


@app.get("/reports/monthly/xlsx")
def export_monthly_xlsx(
    year: int,
    month: int,
    car_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")

    resolved_car_id = car_id or _first_vehicle_id(db)
    if not resolved_car_id:
        raise HTTPException(status_code=400, detail="No vehicle found.")

    file_path = generate_monthly_report_xlsx(db, resolved_car_id, year, month)
    filename = Path(file_path).name

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/reports/monthly/pdf")
def export_monthly_pdf(
    year: int,
    month: int,
    car_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")

    resolved_car_id = car_id or _first_vehicle_id(db)
    if not resolved_car_id:
        raise HTTPException(status_code=400, detail="No vehicle found.")

    file_path = generate_monthly_report_pdf(db, resolved_car_id, year, month)
    filename = Path(file_path).name

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/pdf",
    )


@app.get("/reports/monthly/preview")
def preview_monthly(
    year: int,
    month: int,
    car_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")

    resolved_car_id = car_id or _first_vehicle_id(db)
    if not resolved_car_id:
        raise HTTPException(status_code=400, detail="No vehicle found.")

    _, last_day = calendar.monthrange(year, month)
    end_of_month = date(year, month, last_day)

    reports = (
        db.query(DailyReport)
        .filter(
            DailyReport.car_id == resolved_car_id,
            DailyReport.drive_date >= date(year, month, 1),
            DailyReport.drive_date <= end_of_month,
        )
        .all()
    )
    manuals = (
        db.query(DailyManualEntry)
        .filter(
            DailyManualEntry.car_id == resolved_car_id,
            DailyManualEntry.drive_date >= date(year, month, 1),
            DailyManualEntry.drive_date <= end_of_month,
        )
        .all()
    )

    return {
        "car_id": resolved_car_id,
        "year": year,
        "month": month,
        "daily_report_count": len(reports),
        "manual_entry_count": len(manuals),
    }
