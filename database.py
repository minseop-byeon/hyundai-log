import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_URL = "sqlite:///./app.db"


def _build_engine():
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        database_url = DEFAULT_SQLITE_URL

    engine_kwargs = {}

    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    elif database_url.startswith("oracle+oracledb"):
        wallet_dir = os.getenv("ORACLE_WALLET_DIR", "").strip()
        wallet_password = os.getenv("ORACLE_WALLET_PASSWORD", "").strip()
        dsn = os.getenv("ORACLE_DSN", "").strip()
        oracle_user = os.getenv("ORACLE_USER", "").strip()
        oracle_password = os.getenv("ORACLE_PASSWORD", "").strip()

        if wallet_dir and dsn and oracle_user and oracle_password:
            resolved_wallet_dir = str((BASE_DIR / wallet_dir).resolve()) if not Path(wallet_dir).is_absolute() else wallet_dir
            connect_args = {
                "user": oracle_user,
                "password": oracle_password,
                "dsn": dsn,
                "config_dir": resolved_wallet_dir,
                "wallet_location": resolved_wallet_dir,
            }
            if wallet_password:
                connect_args["wallet_password"] = wallet_password

            database_url = "oracle+oracledb://@"
            engine_kwargs["connect_args"] = connect_args

        engine_kwargs["pool_pre_ping"] = True

    return create_engine(database_url, **engine_kwargs)


engine = _build_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
