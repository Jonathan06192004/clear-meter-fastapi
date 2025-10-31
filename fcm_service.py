import os
import json
import google.auth.transport.requests
from google.oauth2 import service_account
import requests

# Load environment variables
FCM_PROJECT_ID = os.getenv("FCM_PROJECT_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if not FCM_PROJECT_ID or not SERVICE_ACCOUNT_FILE:
    raise EnvironmentError("❌ Missing FCM_PROJECT_ID or GOOGLE_APPLICATION_CREDENTIALS in environment.")

# FCM endpoint
FCM_URL = f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send"

# Load credentials
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/firebase.messaging"],
)

def get_access_token():
    """Generate OAuth 2.0 access token."""
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token

def send_push_notification(token: str, title: str, body: str):
    if not token:
        print("⚠️ Missing FCM token. Skipping notification.")
        return None

    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json; UTF-8",
    }

    message = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body": body,
            }
        }
    }

    response = requests.post(FCM_URL, headers=headers, json=message)
    print(f"🔔 Notification sent: {response.status_code} - {response.text}")
    return response.json()
