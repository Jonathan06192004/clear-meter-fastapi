from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import SessionLocal
from models import WaterReading

load_dotenv()

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variable for Node backend URL
BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "https://aquameter-backend.onrender.com/api/water-readings"
)

# Request payload schema
class WaterReadingPayload(BaseModel):
    user_id: int
    device_id: int
    reading_5digit: int

@app.post("/bridge/send-reading")
def send_reading(payload: WaterReadingPayload):
    db: Session = SessionLocal()
    try:
        # Save reading directly to PostgreSQL
        reading = WaterReading(
            user_id=payload.user_id,
            device_id=payload.device_id,
            reading_5digit=payload.reading_5digit
        )
        db.add(reading)
        db.commit()
        db.refresh(reading)

        # Forward same data to Node backend
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

@app.get("/")
def root():
    return {"status": "FastAPI Bridge Online", "forward_url": BACKEND_URL}
