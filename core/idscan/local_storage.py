"""
Development-only stand-in for object storage, started inside `runserver` itself.

The first time REUSE needs storage it starts an S3-compatible emulator (moto) on
LOCAL_STORAGE_URL in a background thread of the same process, so there is only one command
to run. Uploaded ID images are held in memory and vanish when the server stops or reloads,
which is fine: REUSE deletes them after registration anyway. It does not check upload
policies or signatures the way real storage does. Never used in production: it only runs
when DEBUG is on and no real ID_BUCKET is set.
"""
import logging
import socket
import threading
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_ready = False
_server = None


class EmulatorMissing(Exception):
    pass


def _listening(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _create_bucket():
    region = settings.AWS_REGION
    client = boto3.client(
        "s3", endpoint_url=settings.LOCAL_STORAGE_URL, region_name=region,
        aws_access_key_id="local", aws_secret_access_key="local",
    )
    location = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
    try:
        client.create_bucket(Bucket=settings.ID_BUCKET, **location)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def ensure_running():
    """Start the emulator once per process. Safe to call on every storage call."""
    global _ready, _server
    if _ready:
        return
    with _lock:
        if _ready:
            return
        address = urlparse(settings.LOCAL_STORAGE_URL)
        if not _listening(address.hostname, address.port):
            try:
                from moto.server import ThreadedMotoServer
            except ImportError as exc:
                raise EmulatorMissing(
                    'Local storage needs the development packages. Run: pip install -r requirements-dev.txt'
                ) from exc
            logging.getLogger("werkzeug").setLevel(logging.WARNING)  # keep runserver's terminal readable
            _server = ThreadedMotoServer(ip_address=address.hostname, port=address.port, verbose=False)
            _server.start()
            log.info("Local ID storage started at %s (development only)", settings.LOCAL_STORAGE_URL)
        _create_bucket()
        _ready = True
