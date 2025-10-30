from sqlalchemy import Column, Integer, DateTime, func
from database import Base

class WaterReading(Base):
    __tablename__ = "water_readings"  # match your real table

    reading_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    device_id = Column(Integer, index=True)
    reading_5digit = Column(Integer, nullable=False)
    previous_reading = Column(Integer, default=0)
    current_reading = Column(Integer, default=0)
    consumption = Column(Integer, default=0)
    timestamp = Column(DateTime(timezone=False), server_default=func.now())
