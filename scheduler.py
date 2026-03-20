import asyncio
from datetime import date, datetime, timedelta

import holidays as pyholidays
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from database import SessionLocal
from hyundai_client import HyundaiClient
from models import DailyReport, OdometerLog, TokenStore

_scheduler = None
_scheduler_car_id = None


def _kr_holidays(year: int):
    return pyholidays.country_holidays("KR", years=[year])


def is_non_working_day(target_date: date) -> bool:
    if target_date.weekday() >= 5:  # Saturday, Sunday
        return True
    if target_date in _kr_holidays(target_date.year):
        return True
    return False


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
    raw_json = refresh_result.get("json") or {}

    new_access_token = raw_json.get("access_token")
    new_refresh_token = raw_json.get("refresh_token")
    expires_in = raw_json.get("expires_in", 3600)

    if not new_access_token:
        print(f"[SCHEDULER] token refresh failed: {refresh_result}")
        return None

    token_row.access_token = new_access_token
    if new_refresh_token:
        token_row.refresh_token = new_refresh_token

    token_row.expires_at = datetime.utcnow() + timedelta(seconds=max(int(expires_in) - 60, 60))
    token_row.updated_at = datetime.utcnow()
    db.commit()

    print("[SCHEDULER] access token refreshed")
    return token_row.access_token


def collect_odometer_job(car_id: str):
    asyncio.run(_collect_odometer(car_id))


def _derive_daily_report_fields(logs: list[OdometerLog]) -> dict | None:
    if not logs:
        return None

    min_value = min(row.odometer_value for row in logs)
    max_value = max(row.odometer_value for row in logs)

    min_rows = [row for row in logs if row.odometer_value == min_value]
    max_rows = [row for row in logs if row.odometer_value == max_value]

    return {
        "start_time": min_rows[-1].log_time if min_rows else None,
        "end_time": max_rows[0].log_time if max_rows else None,
        "odometer_start": min_value,
        "odometer_end": max_value,
        "distance_km": max_value - min_value,
    }


def _upsert_daily_report(db: Session, car_id: str, target_date: date, logs: list[OdometerLog]):
    derived = _derive_daily_report_fields(logs)
    if not derived:
        return None

    existing = (
        db.query(DailyReport)
        .filter(
            DailyReport.car_id == car_id,
            DailyReport.drive_date == target_date,
        )
        .first()
    )

    if existing is None:
        existing = DailyReport(
            car_id=car_id,
            drive_date=target_date,
            is_working_day=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(existing)

    existing.start_time = derived["start_time"]
    existing.end_time = derived["end_time"]
    existing.odometer_start = derived["odometer_start"]
    existing.odometer_end = derived["odometer_end"]
    existing.distance_km = derived["distance_km"]
    existing.is_working_day = True
    existing.updated_at = datetime.utcnow()

    return existing


async def _collect_odometer(car_id: str, force: bool = False):
    db: Session = SessionLocal()
    client = HyundaiClient()

    try:
        local_now = datetime.now()

        if not force and is_non_working_day(local_now.date()):
            return

        # 10:00 ~ 17:00 only
        if not force and (local_now.hour < 10 or local_now.hour > 17):
            return

        access_token = await _ensure_valid_access_token(db, client)
        if not access_token:
            print("[SCHEDULER] valid access token not found")
            return

        result = await client.get_odometer(access_token, car_id)

        if result.get("status_code") == 401:
            text = (result.get("text") or "").lower()
            if "expired" in text:
                access_token = await _ensure_valid_access_token(db, client)
                if access_token:
                    result = await client.get_odometer(access_token, car_id)

        data = result.get("json") or {}
        odometers = data.get("odometers", [])

        if not odometers:
            print(f"[SCHEDULER] no odometer data: {result}")
            return

        latest = odometers[0]
        value = latest.get("value")
        api_timestamp = latest.get("timestamp")

        if value is None:
            print(f"[SCHEDULER] invalid odometer payload: {result}")
            return

        log_time = local_now.strftime("%H:%M")

        existing = (
            db.query(OdometerLog)
            .filter(
                OdometerLog.car_id == car_id,
                OdometerLog.log_date == local_now.date(),
                OdometerLog.log_time == log_time,
            )
            .first()
        )

        if existing is None:
            row = OdometerLog(
                car_id=car_id,
                log_date=local_now.date(),
                log_time=log_time,
                odometer_value=value,
                api_timestamp=api_timestamp,
            )
            db.add(row)
        else:
            existing.odometer_value = value
            existing.api_timestamp = api_timestamp

        logs = (
            db.query(OdometerLog)
            .filter(
                OdometerLog.car_id == car_id,
                OdometerLog.log_date == local_now.date(),
            )
            .order_by(OdometerLog.log_time.asc())
            .all()
        )
        _upsert_daily_report(db, car_id, local_now.date(), logs)
        db.commit()
        print(f"[ODOMETER] {local_now} value={value}")

    except Exception as exc:
        print(f"[SCHEDULER ERROR] {exc}")
        raise
    finally:
        db.close()


def finalize_daily_report_job(car_id: str):
    _finalize_daily_report(car_id, datetime.now().date())


def _finalize_daily_report(car_id: str, target_date: date):
    db: Session = SessionLocal()

    try:
        if is_non_working_day(target_date):
            print(f"[FINALIZE] skipped non-working day: {target_date}")
            return

        logs = (
            db.query(OdometerLog)
            .filter(
                OdometerLog.car_id == car_id,
                OdometerLog.log_date == target_date,
            )
            .order_by(OdometerLog.log_time.asc())
            .all()
        )

        if not logs:
            print(f"[FINALIZE] no logs for {target_date}")
            return

        existing = _upsert_daily_report(db, car_id, target_date, logs)
        db.commit()
        print(
            f"[FINALIZE] {target_date} start={existing.start_time} end={existing.end_time} "
            f"distance={existing.distance_km}"
        )

    finally:
        db.close()


def start_scheduler(car_id: str):
    global _scheduler, _scheduler_car_id

    if _scheduler is not None and _scheduler.running and _scheduler_car_id == car_id:
        return
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        collect_odometer_job,
        "cron",
        hour="10-17",
        minute="*/5",
        args=[car_id],
        id="collect_odometer_job",
        replace_existing=True,
    )

    scheduler.add_job(
        finalize_daily_report_job,
        "cron",
        hour=18,
        minute=0,
        args=[car_id],
        id="finalize_daily_report_job",
        replace_existing=True,
    )

    scheduler.start()
    _scheduler = scheduler
    _scheduler_car_id = car_id
    print(f"[SCHEDULER] started car_id={car_id}")


def stop_scheduler():
    global _scheduler, _scheduler_car_id
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
    _scheduler_car_id = None


def scheduler_status() -> dict:
    return {
        "running": bool(_scheduler is not None and _scheduler.running),
        "car_id": _scheduler_car_id,
    }
