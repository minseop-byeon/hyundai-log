from pathlib import Path

from sqlalchemy import create_engine, text

from database import Base, engine
from models import DailyManualEntry, DailyReport, OdometerLog, VehicleStore  # noqa: F401


BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_SQLITE_PATH = BASE_DIR / "vehicle_log_snapshot.db"
SNAPSHOT_TABLES = (
    "vehicle_store",
    "odometer_logs",
    "daily_reports",
    "daily_manual_entries",
)


def export_snapshot():
    if SNAPSHOT_SQLITE_PATH.exists():
        SNAPSHOT_SQLITE_PATH.unlink()

    target_engine = create_engine(
        f"sqlite:///{SNAPSHOT_SQLITE_PATH.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    try:
        Base.metadata.create_all(bind=target_engine)
        with engine.connect() as source_conn, target_engine.begin() as target_conn:
            for table_name in SNAPSHOT_TABLES:
                rows = source_conn.execute(text(f"SELECT * FROM {table_name}")).mappings().all()
                if not rows:
                    continue
                target_conn.execute(
                    text(
                        f"INSERT INTO {table_name} ({', '.join(rows[0].keys())}) "
                        f"VALUES ({', '.join(':' + key for key in rows[0].keys())})"
                    ),
                    [dict(row) for row in rows],
                )
    finally:
        target_engine.dispose()


if __name__ == "__main__":
    export_snapshot()
