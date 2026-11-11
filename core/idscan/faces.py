"""
Find faces and turn one into a template (a list of numbers) that can be compared with another.
To use another engine, write a class with `name`, `detect(image)` and `template(image, face)`,
and point FACE_PROVIDER at it. Stored templates only match the engine that made them.
"""
import base64
import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class FaceEngineUnavailable(Exception):
    """The face engine cannot run here, for example because its model files are missing."""


@dataclass(frozen=True)
class Face:
    box: tuple        # (left, top, width, height) in pixels
    right_eye: tuple  # the person's right eye, which is on the image's left
    left_eye: tuple
    nose: tuple
    score: float
    row: object = None  # the engine's own record of this face

    @property
    def width(self):
        return self.box[2]

    @property
    def upright(self):
        """Eyes side by side and above the nose: the picture is the right way up."""
        across = self.left_eye[0] - self.right_eye[0]
        down = self.left_eye[1] - self.right_eye[1]
        return across > abs(down) and self.nose[1] > (self.left_eye[1] + self.right_eye[1]) / 2


class OpenCVFaceProvider:
    """YuNet finds faces and SFace turns one into 128 numbers. Both are OpenCV's own models."""

    name = "opencv-sface-2021dec"
    DETECTOR = "face_detection_yunet_2023mar.onnx"
    RECOGNIZER = "face_recognition_sface_2021dec.onnx"
    MIN_SCORE = 0.8

    def __init__(self):
        import cv2

        folder = Path(settings.FACE_MODEL_DIR)
        if not all((folder / name).is_file() for name in (self.DETECTOR, self.RECOGNIZER)):
            raise FaceEngineUnavailable("Face model files are missing. Run: python manage.py fetch_face_models")
        self._detector = cv2.FaceDetectorYN.create(str(folder / self.DETECTOR), "", (320, 320), self.MIN_SCORE, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(folder / self.RECOGNIZER), "")
        self._lock = threading.Lock()  # OpenCV's objects are not safe to share between threads

    def detect(self, image):
        """Faces in a BGR image, largest first."""
        height, width = image.shape[:2]
        with self._lock:
            self._detector.setInputSize((width, height))
            _, rows = self._detector.detect(image)
        faces = []
        for row in rows if rows is not None else ():
            faces.append(Face(
                box=tuple(float(n) for n in row[0:4]),
                right_eye=(float(row[4]), float(row[5])), left_eye=(float(row[6]), float(row[7])),
                nose=(float(row[8]), float(row[9])), score=float(row[14]), row=row,
            ))
        return sorted(faces, key=lambda face: face.box[2] * face.box[3], reverse=True)

    def template(self, image, face):
        with self._lock:
            aligned = self._recognizer.alignCrop(image, face.row)
            vector = self._recognizer.feature(aligned)
        return unit(vector)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32).ravel()
    length = float(np.linalg.norm(vector))
    return vector / length if length else vector


def similarity(a, b):
    """Cosine similarity of two unit-length templates: 1 = identical, around 0 = unrelated."""
    if a is None or b is None or len(a) != len(b):
        return -1.0
    return float(np.dot(a, b))


# ---------------------------------------------------------------------------
# Templates are encrypted before they are stored
# ---------------------------------------------------------------------------
def _fernet():
    key = settings.FACE_TEMPLATE_KEY
    if not key:
        digest = hashlib.sha256(f"face-template:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    try:
        return Fernet(key)
    except ValueError as exc:
        raise ImproperlyConfigured("FACE_TEMPLATE_KEY must be a key made by Fernet.generate_key().") from exc


def seal(vector):
    return _fernet().encrypt(np.asarray(vector, dtype=np.float32).tobytes())


def unseal(blob):
    """Return the template, or None when it cannot be read with the current key."""
    if not blob:
        return None
    try:
        return np.frombuffer(_fernet().decrypt(bytes(blob)), dtype=np.float32)
    except InvalidToken:
        return None
