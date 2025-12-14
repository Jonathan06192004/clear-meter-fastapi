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
# -----------------------
# MODIFICATION: Explicitly cast DB strings to int for calculation
# =======================
def get_daily_consumption_average(db: Session, user_id: int, days: int = 30) -> float:
    start_date = datetime.now() - timedelta(days=days)

    earliest_row = (
        db.query(WaterReading.reading_5digit)
        .filter(
            WaterReading.user_id == user_id,
            WaterReading.timestamp >= start_date
        )
        .order_by(WaterReading.timestamp.asc())
        .first()
    )

    latest_row = (
        db.query(WaterReading.reading_5digit)
        .filter(WaterReading.user_id == user_id)
        .order_by(WaterReading.timestamp.desc())
        .first()
    )

    if not earliest_row or not latest_row:
        return 0.0

    try:
        # ✅ CAST TO INT: reading_5digit is stored as VARCHAR(5)
        earliest_reading = int(earliest_row[0])
        latest_reading = int(latest_row[0])
    except ValueError:
        # Handle case where string is not a valid integer (shouldn't happen if data is clean)
        return 0.0

    total = latest_reading - earliest_reading
    return total / days if total > 0 else 0.0


# =======================
# ROUTE 1: Receive RasPi Reading (RAW MODE)
# -----------------------
# MODIFICATION: Changed how previous_value is determined to be safer
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
        raw_value = payload.reading_5digit.strip()      # "00541"
        
        try:
             current_value = int(raw_value)             # 541
        except ValueError:
             # If the raw value is invalid (e.g., "_____"), reject it
             return {"status": "error", "message": f"Invalid reading format: {raw_value}"}

        # 📌 Last reading (numeric comparison)
        last_reading = (
            db.query(WaterReading)
            .filter(WaterReading.device_id == device_id)
            .order_by(WaterReading.timestamp.desc())
            .first()
        )

        # ⭐️ MODIFIED LOGIC START ⭐️
        # Use the current_reading from the last record if it exists.
        # This is safer than re-casting the string reading_5digit
        previous_value = last_reading.current_reading if last_reading else 0
        
        # Original (Removed due to potential parsing issues on ORM object access):
        # previous_value = (
        #     int(last_reading.reading_5digit)
        #     if last_reading else 0
        # )
        # ⭐️ MODIFIED LOGIC END ⭐️
        
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
        # The exception here is what prevented the subsequent writes!
        # Printing it to the console may help debugging in the future.
        print(f"An error occurred during send_reading: {e}")
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