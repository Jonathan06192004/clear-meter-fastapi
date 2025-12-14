from sqlalchemy import Column, Integer, DateTime, String, func
from database import Base

class WaterReading(Base):
    __tablename__ = "water_readings"

    reading_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    device_id = Column(Integer, index=True)

    # ✅ VARCHAR(5) — preserves leading zeros
    reading_5digit = Column(String(5), nullable=False)

    previous_reading = Column(Integer, default=0)
    current_reading = Column(Integer, default=0)
    consumption = Column(Integer, default=0)

    timestamp = Column(DateTime(timezone=False), server_default=func.now())
