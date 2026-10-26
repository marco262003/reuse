"""Tiny helpers so every endpoint answers with the same JSON shape."""
import json
from functools import wraps

from django.http import JsonResponse


def ok(data=None, status=200):
    payload = {"ok": True}
    payload.update(data or {})
    return JsonResponse(payload, status=status)


def fail(message, status=400, errors=None, **extra):
    payload = {"ok": False, "message": message, "errors": errors or {}}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def api_view(methods, auth=True):
    """Check method + login, and parse the JSON body for write requests."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return fail("Method not allowed.", 405)
            if auth and not request.user.is_authenticated:
                return fail("Please log in to continue.", 401)
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                try:
                    data = json.loads(request.body or b"{}")
                except ValueError:
                    return fail("Invalid request body.", 400)
                if not isinstance(data, dict):
                    return fail("Invalid request body.", 400)
                request.data = data
            return fn(request, *args, **kwargs)

        return wrapper

    return decorator
