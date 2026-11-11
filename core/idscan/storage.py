"""
Object storage for uploaded ID images: any S3-compatible service, reached with boto3.
The browser uploads straight to storage with a pre-signed POST; Django only ever holds
a file in memory while it is being read.
"""
import logging
from functools import lru_cache, wraps

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

log = logging.getLogger(__name__)


class StorageUnavailable(Exception):
    """The storage service could not be reached or refused the call."""


class FileTooLarge(Exception):
    pass


def is_configured():
    return bool(settings.ID_BUCKET)


@lru_cache(maxsize=4)
def _make_client(endpoint_url, region):
    extra = {}
    if endpoint_url.startswith(("http://127.0.0.1", "http://localhost")):
        # The local emulator accepts any credentials, so real keys are never sent to it.
        extra = {"aws_access_key_id": "local", "aws_secret_access_key": "local"}
    return boto3.client(
        "s3", endpoint_url=endpoint_url or None, region_name=region,
        config=Config(
            signature_version="s3v4", connect_timeout=5, read_timeout=20,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"} if endpoint_url else None,
        ),
        **extra,
    )


def _client():
    if settings.LOCAL_STORAGE_AUTOSTART and settings.S3_ENDPOINT_URL == settings.LOCAL_STORAGE_URL:
        from . import local_storage

        try:
            local_storage.ensure_running()
        except Exception as exc:  # noqa: BLE001 - port taken, package missing, emulator error
            log.exception("Local ID storage could not start")
            raise StorageUnavailable(str(exc)) from exc
    return _make_client(settings.S3_ENDPOINT_URL, settings.AWS_REGION)


def _storage_call(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ClientError, BotoCoreError) as exc:
            log.exception("Storage call %s failed", fn.__name__)
            raise StorageUnavailable(str(exc)) from exc

    return wrapper


@_storage_call
def presigned_upload(key, content_type):
    """
    What the browser needs to send one file straight to storage. The signed policy fixes the
    key, the content type and the size range, so the storage service itself refuses anything else.
    """
    post = _client().generate_presigned_post(
        Bucket=settings.ID_BUCKET, Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, settings.ID_UPLOAD_MAX_BYTES]],
        ExpiresIn=settings.UPLOAD_URL_SECONDS,
    )
    return {"url": post["url"], "fields": post["fields"], "expires_in": settings.UPLOAD_URL_SECONDS}


@_storage_call
def fetch(key):
    """Return the file's bytes, or None when nothing was uploaded. Raises FileTooLarge."""
    try:
        found = _client().get_object(Bucket=settings.ID_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    if found["ContentLength"] > settings.ID_UPLOAD_MAX_BYTES:
        raise FileTooLarge
    return found["Body"].read()


@_storage_call
def delete(key):
    _client().delete_object(Bucket=settings.ID_BUCKET, Key=key)


@_storage_call
def move(key, new_key):
    client = _client()
    client.copy_object(Bucket=settings.ID_BUCKET, Key=new_key, CopySource={"Bucket": settings.ID_BUCKET, "Key": key})
    client.delete_object(Bucket=settings.ID_BUCKET, Key=key)
