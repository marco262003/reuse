"""ID upload and reading endpoints for a sign-up in progress (or an account adding its ID)."""
from django.conf import settings

from . import face_login, registration, registration_fields as rf
from .api import api_view, fail, ok
from .services import ServiceError


def service_fail(exc):
    extra = {"code": exc.code} if exc.code else {}
    return fail(exc.message, exc.status, errors=exc.errors, **extra)


@api_view(["GET"], auth=False)
def schema(request):
    return ok({
        "fields": rf.schema(),
        "upload": {"max_bytes": settings.ID_UPLOAD_MAX_BYTES, "types": list(settings.ID_UPLOAD_TYPES)},
    })


@api_view(["POST"], auth=False)
def upload_id(request):
    d = request.data
    try:
        document, upload = registration.start_upload(request, d.get("side"), d.get("content_type"), d.get("size"))
    except ServiceError as exc:
        return service_fail(exc)
    payload = {"document_id": str(document.pk), "upload": upload}
    return ok(payload)


@api_view(["POST"], auth=False)
def extract_id(request):
    try:
        return ok(registration.extract(request, owner=face_login.pending_user(request)))
    except ServiceError as exc:
        return service_fail(exc)
