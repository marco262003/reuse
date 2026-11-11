"""
Download the two model files the OpenCV face engine needs (about 39 MB) into FACE_MODEL_DIR:

    python manage.py fetch_face_models

They come from OpenCV's own model zoo and are checked against known SHA-256 hashes.
"""
import hashlib
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = (
    ("face_detection_yunet_2023mar.onnx", f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
     "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("face_recognition_sface_2021dec.onnx", f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
     "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Download the face detection and recognition models from OpenCV's model zoo."

    def handle(self, *args, **options):
        folder = Path(settings.FACE_MODEL_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        for name, url, expected in MODELS:
            target = folder / name
            if target.is_file() and sha256(target) == expected:
                self.stdout.write(f"{name}: already here")
                continue
            self.stdout.write(f"{name}: downloading...")
            partial = target.with_name(name + ".part")
            try:
                urllib.request.urlretrieve(url, partial)
            except OSError as exc:
                partial.unlink(missing_ok=True)
                raise CommandError(f"Could not download {name}: {exc}") from exc
            if sha256(partial) != expected:
                partial.unlink()
                raise CommandError(f"{name} did not match its expected hash, so it was not kept.")
            partial.replace(target)
            self.stdout.write(self.style.SUCCESS(f"{name}: saved"))
