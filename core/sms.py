"""
SMS sending. Email/SMS only count as delivered when a real provider accepts the
message - the UI alone never counts.

This adapter is written for the Semaphore HTTP API (popular in the Philippines).
Check the provider's current docs and swap this function if you use another one.
"""
import urllib.parse
import urllib.request

from django.conf import settings


class SMSNotConfigured(Exception):
    """Raised when no SMS provider key is set."""


def send_sms(number, message):
    if not settings.SMS_API_KEY:
        raise SMSNotConfigured("SMS provider is not configured.")
    payload = {
        "apikey": settings.SMS_API_KEY,
        "number": number.lstrip("+"),   # +639171234567 -> 639171234567
        "message": message,
    }
    if settings.SMS_SENDER_NAME:
        payload["sendername"] = settings.SMS_SENDER_NAME
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request("https://api.semaphore.co/api/v4/messages", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:  # raises on HTTP errors
        response.read()
