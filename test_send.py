import requests

url = "http://127.0.0.1:8000/bridge/send-reading"
data = {
    "device_id": "101",
    "user_id": 2,
    "reading_value": 54321
}

response = requests.post(url, json=data)
print(response.json())
