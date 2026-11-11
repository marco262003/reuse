"""Turn an uploaded file into an image, and clean a card photo up for OCR."""
import io
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_SIDE = 2000
PDF_DPI = 200
CARD_ASPECT = 1.586  # ISO ID-1 card, long side / short side
SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)


class UnreadableFile(Exception):
    """The bytes are not a usable JPG, PNG or PDF."""


def sniff_type(data):
    """The real file type from its first bytes (its "magic number"), or None."""
    for signature, content_type in SIGNATURES:
        if data.startswith(signature):
            return content_type
    return None


def _first_pdf_page(data):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) < 1:
            raise UnreadableFile("The PDF has no pages.")
        return pdf[0].render(scale=PDF_DPI / 72).to_pil()
    finally:
        pdf.close()


def load_image(data):
    """Return an upright BGR image no larger than MAX_SIDE. Raises UnreadableFile."""
    kind = sniff_type(data)
    if kind is None:
        raise UnreadableFile("Not a JPG, PNG or PDF.")
    try:
        image = _first_pdf_page(data) if kind == "application/pdf" else Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    except UnreadableFile:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow and pdfium raise many types for a damaged file
        raise UnreadableFile(str(exc)) from exc
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def quarter_turns(image, turns):
    return np.ascontiguousarray(np.rot90(image, turns))


def sharpness(image):
    """Variance of the Laplacian: low for a blurry photo."""
    return float(cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


# ---------------------------------------------------------------------------
# OCR preprocessing. Every step returns its input unchanged when it is not sure.
# ---------------------------------------------------------------------------
def crop_card(image):
    """Cut the picture down to the card when its four edges are clearly visible."""
    height, width = image.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        share = cv2.contourArea(contour) / (width * height)
        quad = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if not 0.25 <= share <= 0.95 or len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        corners = quad.reshape(4, 2).astype(np.float32)
        total, spread = corners.sum(axis=1), np.diff(corners, axis=1).ravel()
        ordered = np.float32([corners[total.argmin()], corners[spread.argmin()], corners[total.argmax()], corners[spread.argmax()]])
        out_w = int(max(np.linalg.norm(ordered[0] - ordered[1]), np.linalg.norm(ordered[3] - ordered[2])))
        out_h = int(max(np.linalg.norm(ordered[0] - ordered[3]), np.linalg.norm(ordered[1] - ordered[2])))
        # Only a shape close to a real card is trusted: cutting to a panel inside the card would lose fields.
        if min(out_w, out_h) < 200 or abs(max(out_w, out_h) / min(out_w, out_h) - CARD_ASPECT) > 0.2:
            continue
        target = np.float32([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]])
        return cv2.warpPerspective(image, cv2.getPerspectiveTransform(ordered, target), (out_w, out_h))
    return image


def deskew(image):
    """Level the picture when its long straight lines (card edges, text) agree on a small tilt."""
    height, width = image.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=width // 5, maxLineGap=10)
    if lines is None:
        return image
    angles = [math.degrees(math.atan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in lines.reshape(-1, 4)]
    angles = [angle for angle in angles if abs(angle) <= 15]
    if len(angles) < 3:
        return image
    angle = float(np.median(angles))
    if abs(angle) < 0.5:
        return image
    turn = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(image, turn, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def boost_contrast(image):
    """Even out lighting so faint print stands out (CLAHE on the lightness channel)."""
    light, a, b = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2LAB))
    light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(light)
    return cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2BGR)


def prepare_for_ocr(image):
    for step in (crop_card, deskew, boost_contrast):
        try:
            image = step(image)
        except cv2.error:
            continue
    return image
