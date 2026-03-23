from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from hyundai_client import HyundaiClient
from main import _ensure_valid_access_token, _sync_vehicles_from_api
from models import VehicleStore
from scheduler import _collect_odometer, _finalize_daily_report


def _parse_car_id_list(value: str) -> list[str]:
    items = []
    seen = set()
    for raw in (value or "").replace("\n", ",").split(","):
        car_id = raw.strip()
        if not car_id or car_id in seen:
            continue
        seen.add(car_id)
        items.append(car_id)
    return items


def _resolve_car_ids(db: Session) -> list[str]:
    preferred_car_ids = _parse_car_id_list(os.getenv("HYUNDAI_CAR_IDS", ""))
    if preferred_car_ids:
        return preferred_car_ids

    preferred_car_id = os.getenv("HYUNDAI_CAR_ID", "").strip()
    if preferred_car_id:
        return [preferred_car_id]

    vehicles = db.query(VehicleStore).order_by(VehicleStore.created_at.asc()).all()
    return [vehicle.car_id for vehicle in vehicles if vehicle.car_id]


async def _ensure_vehicles(db: Session) -> list[str]:
    car_ids = _resolve_car_ids(db)
    if car_ids:
        return car_ids

    client = HyundaiClient()
    access_token = await _ensure_valid_access_token(db, client)
    if not access_token:
        print("[JOB] valid access token not found")
        return []

    synced = await _sync_vehicles_from_api(db, client, access_token)
    if not synced:
        print("[JOB] vehicle sync returned no vehicles")
        return []

    car_ids = _resolve_car_ids(db)
    print(f"[JOB] synced {len(synced)} vehicles")
    return car_ids


async def _run(mode: str, force: bool, finalize_lookback_days: int) -> int:
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        car_ids = await _ensure_vehicles(db)
        if not car_ids:
            return 1
    finally:
        db.close()

    if mode == "collect":
        for car_id in car_ids:
            await _collect_odometer(car_id, force=force)
        return 0

    lookback_days = max(1, int(finalize_lookback_days))
    for offset in range(lookback_days):
        target_date = date.today() - timedelta(days=offset)
        for car_id in car_ids:
            _finalize_daily_report(car_id, target_date)
    return 0


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run Hyundai collection/finalize jobs without the web server.")
    parser.add_argument("--mode", choices=("collect", "finalize"), required=True)
    parser.add_argument("--force", action="store_true", help="Ignore weekday/time guard for collection.")
    parser.add_argument(
        "--finalize-lookback-days",
        type=int,
        default=int(os.getenv("FINALIZE_LOOKBACK_DAYS", "2") or "2"),
        help="Finalize today and recent previous days to repair delayed report updates.",
    )
    args = parser.parse_args()

    return asyncio.run(_run(args.mode, args.force, args.finalize_lookback_days))


if __name__ == "__main__":
    raise SystemExit(main())
