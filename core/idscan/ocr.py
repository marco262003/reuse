"""
Read the text on a card. To use another engine, write a class with a `name` and a
`read(image)` method that returns Line objects, and point OCR_PROVIDER at it.
"""
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    text: str
    confidence: float  # 0 to 1
    box: tuple         # (left, top, right, bottom) in pixels

    @property
    def height(self):
        return self.box[3] - self.box[1]


class RapidOCRProvider:
    """PaddleOCR's PP-OCR models run by ONNX Runtime. The models ship inside the rapidocr package."""

    name = "rapidocr-ppocr"

    def __init__(self):
        from rapidocr import RapidOCR

        self._engine = RapidOCR(params={"Global.log_level": "error"})
        self._lock = threading.Lock()  # one read at a time per process

    def read(self, image):
        """image is a BGR array. Lines come back top to bottom."""
        with self._lock:
            result = self._engine(image)
        if result.txts is None:
            return []
        lines = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            xs, ys = [point[0] for point in box], [point[1] for point in box]
            if text.strip():
                lines.append(Line(text.strip(), float(score), (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))))
        return sorted(lines, key=lambda line: (line.box[1], line.box[0]))
