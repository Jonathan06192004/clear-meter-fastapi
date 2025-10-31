from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import SessionLocal
from models import WaterReading
from fcm_service import send_push_notification  # ✅ Import FCM notification helper

load_dotenv()

app = FastAPI(title="AquaMeter FastAPI Bridge", version="1.0")

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
    fcm_token: str


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

        # Forward same reading to Node backend
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
# ROUTE 2: Save FCM Token from app
# =======================
@app.post("/save_token")
def save_fcm_token(data: TokenPayload):
    db: Session = SessionLocal()
    try:
        query = """
        INSERT INTO user_tokens (user_id, fcm_token)
        VALUES (:uid, :token)
        ON CONFLICT (user_id)
        DO UPDATE SET fcm_token = EXCLUDED.fcm_token;
        """
        db.execute(query, {"uid": data.user_id, "token": data.fcm_token})
        db.commit()
        return {"status": "saved", "user_id": data.user_id}

    except Exception as e:
        db.rollback()
        return {"error": str(e)}

    finally:
        db.close()


# =======================
# ROUTE 3: Check abnormal consumption + send alerts
# =======================
@app.post("/check_consumption")
def check_consumption(background_tasks: BackgroundTasks):
    db: Session = SessionLocal()
    try:
        avg_consumption = db.query(func.avg(WaterReading.consumption)).scalar() or 0

        # Find abnormal readings (greater than 150% of average)
        abnormal_readings = db.query(WaterReading).filter(
            WaterReading.consumption > avg_consumption * 1.5
        ).all()

        sent_count = 0
        for reading in abnormal_readings:
            token_row = db.execute(
                "SELECT fcm_token FROM user_tokens WHERE user_id = :uid",
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
# ROUTE 4: Health Check
# =======================
@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": BACKEND_URL}
