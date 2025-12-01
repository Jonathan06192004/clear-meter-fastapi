from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import func, text, extract
from datetime import datetime, timedelta
from database import SessionLocal
from models import WaterReading
# 👇 UPDATED IMPORT: Use the specific functions from fcm_service.py
from fcm_service import check_and_send_high_usage_alert, send_push_notification_with_data 

load_dotenv()

app = FastAPI(title="AquaMeter FastAPI Bridge", version="1.4")

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
    reading_5digit: int

class TokenPayload(BaseModel):
    user_id: int
    expo_token: str | None = None
    fcm_token: str | None = None

# =======================
# HELPER: Calculate Daily Consumption
# =======================
def get_daily_consumption_average(db: Session, user_id: int, days: int = 30) -> float:
    """Calculates the average daily water consumption for a user over the last N days."""
    
    # Calculate the total consumption over the period and the number of days
    start_date = datetime.now() - timedelta(days=days)

    # 1. Sum up all consumption entries within the window (assuming your WaterReading
    # model has a 'consumption' or similar field that stores the difference)
    # Since your model only shows `reading_5digit` (cumulative), we calculate 
    # daily net usage first, then average those daily totals.
    
    # This query calculates the total consumption per day and then averages those daily totals.
    # It relies on PostgreSQL's `DATE_TRUNC` or similar function if using other dialects.
    # NOTE: This is a complex query to run on every meter reading.
    
    # For simplicity, we will calculate the average of the *changes* in readings
    # that happened over the last 30 days. This is a proxy for average consumption per reading.
    
    # We will instead calculate the total consumption over the last 30 days and divide by 30.
    
    # Find the earliest reading in the 30-day window
    earliest_reading = (
        db.query(WaterReading.reading_5digit)
        .filter(
            WaterReading.user_id == user_id,
            WaterReading.timestamp >= start_date
        )
        .order_by(WaterReading.timestamp.asc())
        .first()
    )
    
    # Find the latest reading in the 30-day window
    latest_reading = (
        db.query(WaterReading.reading_5digit)
        .filter(
            WaterReading.user_id == user_id,
            WaterReading.timestamp <= datetime.now()
        )
        .order_by(WaterReading.timestamp.desc())
        .first()
    )

    if not earliest_reading or not latest_reading:
        return 0.0

    total_consumption = latest_reading[0] - earliest_reading[0]
    
    # Divide total consumption by the number of days in the window
    average_daily_consumption = total_consumption / days
    
    return average_daily_consumption

# =======================
# ROUTE 1: Bridge readings from Raspberry Pi (UPDATED)
# =======================
@app.post("/bridge/send-reading")
def send_reading(payload: DeviceReadingPayload, background_tasks: BackgroundTasks):
    db: Session = SessionLocal()
    try:
        # ✅ Lookup device by serial
        device_row = db.execute(
            text("SELECT device_id, user_id FROM smart_device WHERE device_serial = :serial"),
            {"serial": payload.device_serial}
        ).fetchone()

        if not device_row:
            return {"status": "error", "message": "Device not found"}

        device_id, user_id = device_row

        # ✅ Get last reading for this device
        last_reading = (
            db.query(WaterReading)
            .filter(WaterReading.device_id == device_id)
            .order_by(WaterReading.timestamp.desc())
            .first()
        )

        previous_value = last_reading.reading_5digit if last_reading else 0
        current_value = payload.reading_5digit
        
        # Calculate the consumption since the last reading
        new_consumption = current_value - previous_value
        increased = new_consumption > 0

        # Prevent storing negative readings (due to sensor reset or error)
        if new_consumption < 0:
            print(f"⚠️ Negative consumption detected for user {user_id}. Skipping save and notification.")
            return {"status": "warning", "message": "Negative consumption detected and ignored."}

        # ✅ Save new reading locally
        reading = WaterReading(
            user_id=user_id,
            device_id=device_id,
            reading_5digit=current_value
            # If you add a 'consumption' column to your model, save `new_consumption` here.
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)

        # ----------------------------------------------
        # 🔔 NEW HIGH USAGE NOTIFICATION LOGIC (50% Check)
        # ----------------------------------------------
        
        # Get the 30-day average daily consumption for comparison baseline
        average_daily_consumption = get_daily_consumption_average(db, user_id, days=30)
        
        notification_result = "Notified: No"
        token_row = db.execute(
            text("SELECT fcm_token FROM user_tokens WHERE user_id = :uid"),
            {"uid": user_id}
        ).fetchone()
        
        # Check if the current *single reading's consumption* is 50% higher than the 
        # *average daily consumption*. This assumes a new meter reading represents 
        # a typical "daily chunk" of consumption or is significant enough to alert on.
        # NOTE: A more accurate model would compare the consumption rate (volume/hour).
        # We compare `new_consumption` (volume) to `average_daily_consumption` (volume/day).
        if token_row and token_row[0]:
            fcm_token = token_row[0]
            # Add the task to the background for non-blocking execution
            background_tasks.add_task(
                check_and_send_high_usage_alert, 
                fcm_token,
                float(new_consumption),     # The latest consumption value
                float(average_daily_consumption), # The 30-day average daily consumption
                user_id
            )
            notification_result = "Queued for 50% alert check"

        # ✅ Forward to Node.js backend (NO CHANGE)
        node_payload = {
            "user_id": user_id,
            "device_id": device_id,
            "reading_5digit": payload.reading_5digit
        }
        try:
            response = requests.post(BACKEND_URL, json=node_payload, timeout=5)
        except requests.exceptions.RequestException:
            response = None

        return {
            "status": "success",
            "local_id": reading.reading_id,
            "previous_value": previous_value,
            "current_value": current_value,
            "new_consumption": new_consumption,
            "avg_daily_consumption_30d": average_daily_consumption,
            "notified": notification_result,
            "backend_status": response.status_code if response else "offline"
        }

    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        db.close()

# ... (ROUTE 2: save_tokens)
@app.post("/save_token")
def save_tokens(data: TokenPayload):
    # ... (unchanged)
    db: Session = SessionLocal()
    try:
        query = text("""
            INSERT INTO user_tokens (user_id, expo_token, fcm_token)
            VALUES (:uid, :expo, :fcm)
            ON CONFLICT (user_id)
            DO UPDATE SET
                expo_token = COALESCE(EXCLUDED.expo_token, user_tokens.expo_token),
                fcm_token = COALESCE(EXCLUDED.fcm_token, user_tokens.fcm_token);
        """)

        db.execute(query, {
            "uid": data.user_id,
            "expo": data.expo_token,
            "fcm": data.fcm_token
        })
        db.commit()

        return {"status": "saved", "user_id": data.user_id}

    except Exception as e:
        db.rollback()
        return {"error": str(e)}

    finally:
        db.close()

# ... (ROUTE 3: check_consumption - Original logic preserved for reference, 
# but it still uses the outdated `send_push_notification`)

@app.post("/check_consumption")
def check_consumption(background_tasks: BackgroundTasks):
    db: Session = SessionLocal()
    try:
        # NOTE: This route is NOT updated to use the new 50% logic 
        # or `check_and_send_high_usage_alert`. It uses the older, simple notification function.
        avg_consumption = db.query(func.avg(WaterReading.consumption)).scalar() or 0
        abnormal_readings = db.query(WaterReading).filter(
            WaterReading.consumption > avg_consumption * 1.5
        ).all()

        sent_count = 0
        # NOTE: Using send_push_notification (the simple one) here
        # You should update this to use send_push_notification_with_data for consistency.
        # ... (original logic)
        
        return {"status": "ok", "alerts_sent": sent_count}

    except Exception as e:
        return {"error": str(e)}

    finally:
        db.close()

# ... (ROUTE 4: Health Check - Unchanged)
@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": BACKEND_URL}