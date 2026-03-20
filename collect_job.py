from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from hyundai_client import HyundaiClient
from main import _ensure_valid_access_token, _sync_vehicles_from_api
from models import VehicleStore
from scheduler import _collect_odometer, _finalize_daily_report


def _resolve_car_id(db: Session) -> str | None:
    preferred_car_id = os.getenv("HYUNDAI_CAR_ID", "").strip()
    if preferred_car_id:
        return preferred_car_id

    vehicle = db.query(VehicleStore).order_by(VehicleStore.created_at.asc()).first()
    return vehicle.car_id if vehicle else None


async def _ensure_vehicle(db: Session) -> str | None:
    car_id = _resolve_car_id(db)
    if car_id:
        return car_id

    client = HyundaiClient()
    access_token = await _ensure_valid_access_token(db, client)
    if not access_token:
        print("[JOB] valid access token not found")
        return None

    synced = await _sync_vehicles_from_api(db, client, access_token)
    if not synced:
        print("[JOB] vehicle sync returned no vehicles")
        return None

    car_id = _resolve_car_id(db)
    print(f"[JOB] synced {len(synced)} vehicles")
    return car_id


async def _run(mode: str, force: bool) -> int:
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        car_id = await _ensure_vehicle(db)
        if not car_id:
            return 1
    finally:
        db.close()

    if mode == "collect":
        await _collect_odometer(car_id, force=force)
        return 0

    _finalize_daily_report(car_id, date.today())
    return 0


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run Hyundai collection/finalize jobs without the web server.")
    parser.add_argument("--mode", choices=("collect", "finalize"), required=True)
    parser.add_argument("--force", action="store_true", help="Ignore weekday/time guard for collection.")
    args = parser.parse_args()

    return asyncio.run(_run(args.mode, args.force))


if __name__ == "__main__":
    raise SystemExit(main())
