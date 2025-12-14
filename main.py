from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from datetime import datetime, timedelta

from database import SessionLocal
from models import WaterReading
from fcm_service import check_and_send_high_usage_alert

load_dotenv()

app = FastAPI(title="AquaMeter FastAPI Bridge", version="1.5")

# =======================
# CORS Configuration
# =======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =======================
# Environment Variables
# =======================
BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "https://clear-meter-fastapi-8z5e.onrender.com/api/water-readings"
)

# =======================
# Schemas
# =======================
class DeviceReadingPayload(BaseModel):
    device_serial: str
    reading_5digit: str


class TokenPayload(BaseModel):
    user_id: int
    expo_token: str | None = None
    fcm_token: str | None = None


# =======================
# HELPER: Average Daily Consumption (30 days)
# =======================
def get_daily_consumption_average(db: Session, user_id: int, days: int = 30) -> float:
    start_date = datetime.now() - timedelta(days=days)

    earliest = (
        db.query(WaterReading.reading_5digit)
        .filter(
            WaterReading.user_id == user_id,
            WaterReading.timestamp >= start_date
        )
        .order_by(WaterReading.timestamp.asc())
        .first()
    )

    latest = (
        db.query(WaterReading.reading_5digit)
        .filter(WaterReading.user_id == user_id)
        .order_by(WaterReading.timestamp.desc())
        .first()
    )

    if not earliest or not latest:
        return 0.0

    total = latest[0] - earliest[0]
    return total / days if total > 0 else 0.0


# =======================
# ROUTE 1: Receive RasPi Reading (RAW MODE)
# =======================
@app.post("/bridge/send-reading")
def send_reading(payload: DeviceReadingPayload, background_tasks: BackgroundTasks):
    db: Session = SessionLocal()

    try:
        # 🔍 Find device
        device_row = db.execute(
            text("""
                SELECT device_id, user_id
                FROM smart_device
                WHERE device_serial = :serial
            """),
            {"serial": payload.device_serial}
        ).fetchone()

        if not device_row:
            return {"status": "error", "message": "Device not found"}

        device_id, user_id = device_row

        # 🔢 RAW + INT VALUES
        raw_value = payload.reading_5digit.strip()     # "00541"
        current_value = int(raw_value)                 # 541

        # 📌 Last reading (numeric comparison)
        last_reading = (
            db.query(WaterReading)
            .filter(WaterReading.device_id == device_id)
            .order_by(WaterReading.timestamp.desc())
            .first()
        )

        previous_value = (
            int(last_reading.reading_5digit)
            if last_reading else 0
        )

        new_consumption = current_value - previous_value

        # ⚠️ OCR jitter protection
        if new_consumption < 0:
            print(
                f"⚠️ OCR jitter allowed | prev={previous_value} curr={current_value}"
            )
            new_consumption = 0

        # 💾 SAVE EXACT OCR VALUE
        reading = WaterReading(
            user_id=user_id,
            device_id=device_id,
            reading_5digit=raw_value,   # ✅ STORED AS "00541"
            previous_reading=previous_value,
            current_reading=current_value,
            consumption=new_consumption
        )

        db.add(reading)
        db.commit()
        db.refresh(reading)

        # ----------------------------------
        # 🔔 High Usage Alert
        # ----------------------------------
        avg_daily = get_daily_consumption_average(db, user_id)

        token_row = db.execute(
            text("SELECT fcm_token FROM user_tokens WHERE user_id = :uid"),
            {"uid": user_id}
        ).fetchone()

        if token_row and token_row[0] and new_consumption > 0:
            background_tasks.add_task(
                check_and_send_high_usage_alert,
                token_row[0],
                float(new_consumption),
                float(avg_daily),
                user_id
            )

        # ----------------------------------
        # 🌐 Forward to Node Backend
        # ----------------------------------
        try:
            response = requests.post(
                BACKEND_URL,
                json={
                    "user_id": user_id,
                    "device_id": device_id,
                    "reading_5digit": raw_value  # ✅ KEEP STRING
                },
                timeout=5
            )
            backend_status = response.status_code
        except requests.exceptions.RequestException:
            backend_status = "offline"

        return {
            "status": "success",
            "reading_id": reading.reading_id,
            "raw": raw_value,
            "previous": previous_value,
            "current": current_value,
            "delta": new_consumption,
            "backend": backend_status
        }

    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 2: Save Push Tokens
# =======================
@app.post("/save_token")
def save_tokens(data: TokenPayload):
    db: Session = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO user_tokens (user_id, expo_token, fcm_token)
                VALUES (:uid, :expo, :fcm)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    expo_token = COALESCE(EXCLUDED.expo_token, user_tokens.expo_token),
                    fcm_token = COALESCE(EXCLUDED.fcm_token, user_tokens.fcm_token);
            """),
            {
                "uid": data.user_id,
                "expo": data.expo_token,
                "fcm": data.fcm_token
            }
        )
        db.commit()
        return {"status": "saved", "user_id": data.user_id}

    except Exception as e:
        db.rollback()
        return {"error": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 3: Health Check
# =======================
@app.get("/")
def root():
    return {
        "status": "FastAPI Bridge Online",
        "mode": "RAW_READING_ENABLED",
        "forward_url": BACKEND_URL
    }
