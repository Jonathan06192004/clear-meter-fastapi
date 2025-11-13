from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from database import SessionLocal
from models import WaterReading
from fcm_service import send_push_notification  # ✅ Import FCM helper

load_dotenv()

app = FastAPI(title="AquaMeter FastAPI Bridge", version="1.2")

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
class WaterReadingPayload(BaseModel):
    user_id: int
    device_id: int
    reading_5digit: int


class TokenPayload(BaseModel):
    user_id: int
    expo_token: str | None = None
    fcm_token: str | None = None


# =======================
# ROUTE 1: Bridge readings + Notify on increase
# =======================
@app.post("/bridge/send-reading")
def send_reading(payload: WaterReadingPayload, background_tasks: BackgroundTasks):
    db: Session = SessionLocal()
    try:
        # ✅ Get the last reading for this device
        last_reading = (
            db.query(WaterReading)
            .filter(WaterReading.device_id == payload.device_id)
            .order_by(WaterReading.timestamp.desc())
            .first()
        )

        previous_value = last_reading.reading_5digit if last_reading else 0
        increased = payload.reading_5digit > previous_value

        # ✅ Save new reading locally
        reading = WaterReading(
            user_id=payload.user_id,
            device_id=payload.device_id,
            reading_5digit=payload.reading_5digit
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)

        # ✅ Forward to Node backend
        node_payload = {
            "user_id": payload.user_id,
            "device_id": payload.device_id,
            "reading_5digit": payload.reading_5digit
        }
        try:
            response = requests.post(BACKEND_URL, json=node_payload, timeout=5)
        except requests.exceptions.RequestException:
            response = None

        # ✅ Notify if consumption increased
        if increased:
            token_row = db.execute(
                text("SELECT fcm_token FROM user_tokens WHERE user_id = :uid"),
                {"uid": payload.user_id}
            ).fetchone()

            if token_row and token_row[0]:
                background_tasks.add_task(
                    send_push_notification,
                    token_row[0],
                    "🚰 Water Consumption Increased",
                    f"Your water consumption has increased from {previous_value} to {payload.reading_5digit}. Please check your recent usage."
                )

        return {
            "status": "success",
            "local_id": reading.reading_id,
            "previous_value": previous_value,
            "increased": increased,
            "notified": increased,
            "backend_status": response.status_code if response else "offline"
        }

    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 2: Save Expo + FCM Token
# =======================
@app.post("/save_token")
def save_tokens(data: TokenPayload):
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


# =======================
# ROUTE 3: Manual Check (Optional)
# =======================
@app.post("/check_consumption")
def check_consumption(background_tasks: BackgroundTasks):
    db: Session = SessionLocal()
    try:
        avg_consumption = db.query(func.avg(WaterReading.consumption)).scalar() or 0
        abnormal_readings = db.query(WaterReading).filter(
            WaterReading.consumption > avg_consumption * 1.5
        ).all()

        sent_count = 0
        for reading in abnormal_readings:
            token_row = db.execute(
                text("SELECT fcm_token FROM user_tokens WHERE user_id = :uid"),
                {"uid": reading.user_id}
            ).fetchone()

            if token_row and token_row[0]:
                background_tasks.add_task(
                    send_push_notification,
                    token_row[0],
                    "⚠️ High Water Usage Alert",
                    f"Your water usage is higher than usual. Reading ID: {reading.reading_id}"
                )
                sent_count += 1

        return {"status": "ok", "alerts_sent": sent_count}

    except Exception as e:
        return {"error": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 4: Health Check
# =======================
@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": BACKEND_URL}
