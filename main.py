from fastapi import FastAPI
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base
from models import WaterReading

load_dotenv()

app = FastAPI()

# Create tables if not exist
Base.metadata.create_all(bind=engine)

NODE_BACKEND_URL = os.getenv(
    "NODE_BACKEND_URL",
    "https://aquameter-backend.onrender.com/api/water-readings"
)

class WaterReadingPayload(BaseModel):
    user_id: int
    device_id: int
    reading_5digit: int  # local IoT-style reading (5-digit format)

@app.post("/bridge/send-reading")
def send_reading(payload: WaterReadingPayload):
    db: Session = SessionLocal()

    try:
        # 1️⃣ Save locally in FastAPI bridge
        reading = WaterReading(
            user_id=payload.user_id,
            device_id=payload.device_id,
            reading_5digit=payload.reading_5digit
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)

        # 2️⃣ Prepare payload for Node backend (matches backend route fields)
        node_payload = {
            "user_id": payload.user_id,
            "device_id": payload.device_id,
            "reading_value": payload.reading_5digit  # Node expects this key
        }

        # 3️⃣ Forward to Node backend
        response = requests.post(
            NODE_BACKEND_URL,
            json=node_payload,
            timeout=10
        )

        # 4️⃣ Return result summary
        return {
            "status": "success",
            "local_id": reading.id,
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

@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": NODE_BACKEND_URL}
