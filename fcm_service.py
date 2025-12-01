import os
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
# SEND PUSH NOTIFICATION (Updated to include 'data' payload)
# =======================
def send_push_notification_with_data(token: str, title: str, body: str, data: dict = None):
    """Sends an FCM notification, including an optional data payload."""
    if not token:
        print("⚠️ Missing FCM token. Skipping notification.")
        return {"status": "skipped", "reason": "missing_token"}

    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json; UTF-8",
    }
    
    # Message now includes the optional 'data' field for app-side logic
    message = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body": body,
            },
            "data": data or {}
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

# =======================
# NEW: CHECK AND SEND LOGIC (50% ALERT)
# =======================
def check_and_send_high_usage_alert(token: str, new_reading: float, average_reading: float, user_id: int):
    """
    Compares the new reading (consumption since last reading) to the average daily consumption 
    and sends a push notification if the increase is greater than 50%.
    """
    if not token:
        print(f"⚠️ Missing token for user {user_id}. Cannot send high usage alert.")
        return {"status": "skipped", "reason": "missing_token"}

    # Calculate the percentage increase
    if average_reading <= 0:
        # Treat any positive consumption as an anomaly if baseline is zero/near-zero
        increase_percentage = float('inf') if new_reading > 0 else 0
    else:
        increase_percentage = ((new_reading - average_reading) / average_reading) * 100

    # 🚨 Check the 50% threshold
    if increase_percentage > 50.0:
        title = "⚡️ High Usage Alert!"
        body = (
            f"Your latest consumption ({new_reading:.2f} units) is "
            f"{increase_percentage:.0f}% higher than your average daily usage ({average_reading:.2f} units)."
        )
        
        # Custom data payload for app-side handling
        data_payload = {
            "type": "HIGH_USAGE_ALERT",
            "new_reading": str(f"{new_reading:.2f}"),
            "average_reading": str(f"{average_reading:.2f}"),
            "increase_percent": str(f"{increase_percentage:.0f}")
        }

        # Use the updated send function
        result = send_push_notification_with_data(token, title, body, data_payload)
        
        print(f"Alert sent to user {user_id}: {result['status']}")
        return result
    else:
        print(f"✅ Usage within normal limits for user {user_id}. Increase: {increase_percentage:.2f}%.")
        return {"status": "no_alert_needed", "increase_percent": increase_percentage}