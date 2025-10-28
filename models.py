from sqlalchemy import Column, Integer, DateTime, func
from database import Base

class WaterReading(Base):
    __tablename__ = "water_readings_bridge"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    device_id = Column(Integer, index=True)
    reading_5digit = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
