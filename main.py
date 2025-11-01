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
    "https://aquameter-backend.onrender.com/api/water-readings"
)

# =======================
# Pydantic Schemas
# =======================
class WaterReadingPayload(BaseModel):
    user_id: int
    device_id: int
    reading_5digit: int


class TokenPayload(BaseModel):
    user_id: int
    expo_token: str | None = None
    fcm_token: str | None = None


class NotificationPayload(BaseModel):
    user_id: int
    title: str
    message: str


# =======================
# ROUTE 1: Bridge readings to backend
# =======================
@app.post("/bridge/send-reading")
def send_reading(payload: WaterReadingPayload):
    db: Session = SessionLocal()
    try:
        reading = WaterReading(
            user_id=payload.user_id,
            device_id=payload.device_id,
            reading_5digit=payload.reading_5digit
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)

        node_payload = {
            "user_id": payload.user_id,
            "device_id": payload.device_id,
            "reading_5digit": payload.reading_5digit
        }

        response = requests.post(BACKEND_URL, json=node_payload, timeout=5)

        return {
            "status": "success",
            "local_id": reading.reading_id,
            "forward_to_node": True,
            "backend_status": response.status_code,
            "backend_response": response.json()
        }

    except requests.exceptions.RequestException as req_err:
        db.rollback()
        return {
            "status": "error",
            "message": f"Failed to reach Node backend: {str(req_err)}",
            "local_only": True
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
# ROUTE 3: Manual Notification Test Endpoint (for Hoppscotch)
# =======================
@app.post("/send_notification")
def send_notification(payload: NotificationPayload):
    db: Session = SessionLocal()
    try:
        token_row = db.execute(
            text("SELECT fcm_token FROM user_tokens WHERE user_id = :uid"),
            {"uid": payload.user_id}
        ).fetchone()

        if not token_row or not token_row[0]:
            return {"error": "No FCM token found for this user"}

        fcm_token = token_row[0]
        success = send_push_notification(fcm_token, payload.title, payload.message)

        return {
            "status": "sent" if success else "failed",
            "user_id": payload.user_id,
            "title": payload.title,
            "message": payload.message
        }

    except Exception as e:
        return {"error": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 4: Check abnormal consumption + send FCM Alerts
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
                    "High Water Usage Alert 💧",
                    f"Your water usage today is significantly higher than usual (Reading ID: {reading.reading_id})."
                )
                sent_count += 1

        return {"status": "ok", "alerts_sent": sent_count}

    except Exception as e:
        return {"error": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 5: Health Check
# =======================
@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": BACKEND_URL}
