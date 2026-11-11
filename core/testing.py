"""
Stand-ins for storage, OCR and the face engine, so the tests run without models or a network.

Test pictures are flat colours that the fake face engine "reads":
red = how many faces, green = how far the head is turned, blue = whose face it is.
"""
import base64
import io
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np
from PIL import Image

from .idscan import faces, storage
from .idscan.ocr import Line

ANA, BEN = 10, 20  # two different people


def picture(faces=1, person=ANA, turn=0.0, size=(200, 160), fmt="PNG"):
    out = io.BytesIO()
    Image.new("RGB", size, (faces * 50, 128 + round(turn * 200), person)).save(out, fmt)
    return out.getvalue()


def frame(**kwargs):
    """A camera frame the way the browser sends it."""
    return "data:image/png;base64," + base64.b64encode(picture(**kwargs)).decode()


def _colour(image):
    height, width = image.shape[:2]
    blue, green, red = (int(n) for n in image[height // 2, width // 2])
    return red, green, blue, width, height


class FakeFaces:
    name = "fake-faces"

    def detect(self, image):
        red, green, blue, width, height = _colour(image)
        turn = (green - 128) / 200
        right_eye, left_eye = (width * 0.35, height * 0.4), (width * 0.65, height * 0.4)
        nose = (right_eye[0] + (0.5 + turn) * (left_eye[0] - right_eye[0]), height * 0.55)
        found = faces.Face(
            box=(width * 0.25, height * 0.2, width * 0.5, height * 0.6),
            right_eye=right_eye, left_eye=left_eye, nose=nose, score=0.99, row=round(blue / 10),
        )
        return [found] * round(red / 50)

    def template(self, image, face):
        vector = np.zeros(8, dtype=np.float32)
        vector[face.row % 8] = 1.0
        return vector


def template_for(person):
    """The sealed template FakeFaces would make from this person's ID."""
    vector = np.zeros(8, dtype=np.float32)
    vector[(person // 10) % 8] = 1.0
    return faces.seal(vector)


class FakeOCR:
    """Returns the lines set for a picture of a given width (`pages`), or `lines` for any other."""

    name = "fake-ocr"
    lines = []
    pages = {}
    fail = False

    def read(self, image):
        if FakeOCR.fail:
            raise RuntimeError("OCR engine crashed")
        return FakeOCR.pages.get(image.shape[1], FakeOCR.lines)

    @classmethod
    def reset(cls):
        cls.lines, cls.pages, cls.fail = [], {}, False


def lines(*texts, confidence=0.95):
    """OCR lines stacked top to bottom."""
    return [Line(text, confidence, (10.0, 10.0 + 40 * i, 400.0, 40.0 + 40 * i)) for i, text in enumerate(texts)]


@contextmanager
def fake_storage():
    """Replace object storage with a dict. Yields it: {key: bytes}."""
    files = {}

    def presigned_upload(key, content_type):
        return {"url": "https://storage.test/bucket", "fields": {"key": key, "Content-Type": content_type}, "expires_in": 300}

    def move(key, new_key):
        files[new_key] = files.pop(key)

    with patch.multiple(
        storage, is_configured=lambda: True, presigned_upload=presigned_upload,
        fetch=lambda key: files.get(key), delete=lambda key: files.pop(key, None), move=move,
    ):
        yield files


FAKE_ENGINES = {
    "OCR_PROVIDER": "core.testing.FakeOCR",
    "FACE_PROVIDER": "core.testing.FakeFaces",
}
