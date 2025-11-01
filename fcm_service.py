import os
import json
import time
import google.auth.transport.requests
from google.oauth2 import service_account
import requests

# =======================
# Environment Variables
# =======================
FCM_PROJECT_ID = os.getenv("FCM_PROJECT_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if not FCM_PROJECT_ID or not SERVICE_ACCOUNT_FILE:
    raise EnvironmentError("❌ Missing FCM_PROJECT_ID or GOOGLE_APPLICATION_CREDENTIALS in environment.")

# FCM endpoint
FCM_URL = f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send"

# =======================
# Load Credentials
# =======================
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/firebase.messaging"],
)

_last_token_time = 0
_cached_token = None


# =======================
# ACCESS TOKEN HANDLER
# =======================
def get_access_token(force_refresh: bool = False) -> str:
    """
    Generates and caches the OAuth 2.0 access token for Firebase Cloud Messaging.
    Refreshes only if older than 50 minutes (tokens expire every 60 minutes).
    """
    global _cached_token, _last_token_time

    if not force_refresh and _cached_token and time.time() - _last_token_time < 3000:
        return _cached_token

    try:
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        _cached_token = credentials.token
        _last_token_time = time.time()
        print("🔑 New FCM access token generated.")
        return _cached_token
    except Exception as e:
        print(f"❌ Failed to get FCM access token: {e}")
        raise


# =======================
# SEND PUSH NOTIFICATION
# =======================
def send_push_notification(token: str, title: str, body: str):
    if not token:
        print("⚠️ Missing FCM token. Skipping notification.")
        return {"status": "skipped", "reason": "missing_token"}

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

    try:
        response = requests.post(FCM_URL, headers=headers, json=message)
        status = response.status_code

        # ✅ Handle success
        if status == 200:
            print(f"✅ Notification sent to {token[:12]}... | {status}")
            return {"status": "success", "response": response.json()}

        # ⚠️ Handle invalid/expired token
        elif status == 404 or "InvalidArgument" in response.text:
            print(f"⚠️ Invalid FCM token detected: {token[:12]}... Removing from DB recommended.")
            return {"status": "invalid_token", "response": response.text}

        # 🔁 Handle unauthorized (token expired)
        elif status == 401:
            print("🔄 Access token expired. Refreshing and retrying...")
            headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True)}"
            retry_response = requests.post(FCM_URL, headers=headers, json=message)
            if retry_response.status_code == 200:
                print(f"✅ Retried successfully for {token[:12]}...")
                return {"status": "success", "response": retry_response.json()}
            else:
                print(f"❌ Retry failed ({retry_response.status_code}): {retry_response.text}")
                return {"status": "failed_retry", "response": retry_response.text}

        # ❌ Other unexpected errors
        else:
            print(f"❌ Failed to send notification ({status}): {response.text}")
            return {"status": "error", "response": response.text}

    except requests.exceptions.RequestException as e:
        print(f"🚨 Network error while sending notification: {e}")
        return {"status": "network_error", "response": str(e)}
