from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, UniqueConstraint

from database import Base


class TokenStore(Base):
    __tablename__ = "token_store"

    id = Column(Integer, primary_key=True)
    access_token = Column(String(4000), nullable=True)
    refresh_token = Column(String(4000), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VehicleStore(Base):
    __tablename__ = "vehicle_store"

    id = Column(Integer, primary_key=True)
    car_id = Column(String(128), unique=True, nullable=False)
    car_name = Column(String(255), nullable=True)
    car_nickname = Column(String(255), nullable=True)
    car_sellname = Column(String(255), nullable=True)
    car_type = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OdometerLog(Base):
    __tablename__ = "odometer_logs"
    __table_args__ = (
        UniqueConstraint("car_id", "log_date", "log_time", name="uq_car_time"),
    )

    id = Column(Integer, primary_key=True)
    car_id = Column(String(128), nullable=False, index=True)
    log_date = Column(Date, nullable=False, index=True)
    log_time = Column(String(5), nullable=False, index=True)  # HH:MM
    odometer_value = Column(Integer, nullable=False)
    api_timestamp = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("car_id", "drive_date", name="uq_daily_car_date"),
    )

    id = Column(Integer, primary_key=True)
    car_id = Column(String(128), nullable=False, index=True)
    drive_date = Column(Date, nullable=False, index=True)

    start_time = Column(String(5), nullable=True)  # HH:MM
    end_time = Column(String(5), nullable=True)  # HH:MM

    odometer_start = Column(Integer, nullable=True)
    odometer_end = Column(Integer, nullable=True)
    distance_km = Column(Integer, nullable=True)

    is_working_day = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DailyManualEntry(Base):
    __tablename__ = "daily_manual_entries"
    __table_args__ = (
        UniqueConstraint("car_id", "drive_date", name="uq_manual_car_date"),
    )

    id = Column(Integer, primary_key=True)
    car_id = Column(String(128), nullable=False, index=True)
    drive_date = Column(Date, nullable=False, index=True)

    passenger_name = Column(String(255), nullable=True)
    start_time = Column(String(5), nullable=True)  # HH:MM
    end_time = Column(String(5), nullable=True)  # HH:MM
    odometer_start = Column(Integer, nullable=True)
    odometer_end = Column(Integer, nullable=True)
    distance_km = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
