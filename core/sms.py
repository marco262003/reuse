"""
SMS sending. Email/SMS only count as delivered when a real provider accepts the
message - the UI alone never counts.

This adapter uses the textbee.dev HTTP API: textbee queues the message and your
registered Android phone sends it through its SIM. A 200 reply means textbee
accepted the message, not that it was delivered, and the phone must stay online.
"""
import json
import urllib.request

from django.conf import settings


class SMSNotConfigured(Exception):
    """Raised when no SMS provider key is set."""


def send_sms(number, message):
    if not settings.TEXTBEE_API_KEY:
        raise SMSNotConfigured("SMS provider is not configured.")
    payload = {
        "recipients": [number],   # +639171234567 (international format is accepted as-is)
        "message": message,
    }
    if settings.TEXTBEE_DEVICE_ID:
        payload["deviceId"] = settings.TEXTBEE_DEVICE_ID
    req = urllib.request.Request(
        f"{settings.TEXTBEE_BASE_URL.rstrip('/')}/gateway/send-sms",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "x-api-key": settings.TEXTBEE_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "reuse-system/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as response:  # raises on HTTP errors (400/401/429...)
        body = json.loads(response.read() or b"{}")
    if body.get("success") is False:
        raise RuntimeError(f"textbee rejected the message: {body.get('error') or body.get('message') or body}")
