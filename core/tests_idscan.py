"""
Run with:  python manage.py test core.tests_idscan
The services on their own: storage, file reading, OCR preprocessing, field mapping, liveness.
Tests that need the real model files are skipped when they have not been downloaded.
"""
import base64
import io
import json
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import boto3
import cv2
import numpy as np
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import Stubber
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from PIL import Image, ImageDraw

from .idscan import faces, liveness, mapping, preprocess, storage
from .testing import lines, picture

TEMPLATE = settings.ID_TEMPLATES["batstateu"]
MODELS_HERE = all((Path(settings.FACE_MODEL_DIR) / name).is_file()
                  for name in (faces.OpenCVFaceProvider.DETECTOR, faces.OpenCVFaceProvider.RECOGNIZER))


def values(extraction):
    return {name: item["value"] for name, item in extraction.fields.items()}


@override_settings(ID_BUCKET="id-bucket", S3_ENDPOINT_URL="", AWS_REGION="ap-southeast-1")
class StorageTests(SimpleTestCase):
    """botocore's Stubber checks every call against Amazon's published S3 API."""

    def setUp(self):
        self.s3 = boto3.client("s3", region_name="ap-southeast-1", aws_access_key_id="x", aws_secret_access_key="y",
                               config=Config(signature_version="s3v4"))
        self.stub = Stubber(self.s3)
        self.stub.activate()
        patcher = patch.object(storage, "_client", return_value=self.s3)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.stub.assert_no_pending_responses)

    def test_upload_policy_fixes_key_type_and_size(self):
        upload = storage.presigned_upload("pending/d1/id/front-1.jpg", "image/jpeg")
        conditions = json.loads(base64.b64decode(upload["fields"]["policy"]))["conditions"]
        self.assertIn({"key": "pending/d1/id/front-1.jpg"}, conditions)
        self.assertIn({"bucket": "id-bucket"}, conditions)
        self.assertIn({"Content-Type": "image/jpeg"}, conditions)
        self.assertIn(["content-length-range", 1, settings.ID_UPLOAD_MAX_BYTES], conditions)
        self.assertEqual(upload["fields"]["key"], "pending/d1/id/front-1.jpg")
        self.assertTrue(upload["url"].startswith("https://id-bucket.s3"))
        self.assertEqual(upload["expires_in"], settings.UPLOAD_URL_SECONDS)

    def test_fetch(self):
        body = b"file bytes"
        self.stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(body), len(body)), "ContentLength": len(body)},
                               {"Bucket": "id-bucket", "Key": "k"})
        self.assertEqual(storage.fetch("k"), body)
        self.stub.add_client_error("get_object", "NoSuchKey")
        self.assertIsNone(storage.fetch("missing"))
        self.stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b""), 0), "ContentLength": settings.ID_UPLOAD_MAX_BYTES + 1})
        with self.assertRaises(storage.FileTooLarge):
            storage.fetch("huge")

    def test_delete_move_and_failures(self):
        self.stub.add_response("delete_object", {}, {"Bucket": "id-bucket", "Key": "k"})
        storage.delete("k")
        self.stub.add_response("copy_object", {}, {"Bucket": "id-bucket", "Key": "new", "CopySource": {"Bucket": "id-bucket", "Key": "old"}})
        self.stub.add_response("delete_object", {}, {"Bucket": "id-bucket", "Key": "old"})
        storage.move("old", "new")
        self.stub.add_client_error("delete_object", "AccessDenied")
        with self.assertLogs("core.idscan.storage", "ERROR"), self.assertRaises(storage.StorageUnavailable):
            storage.delete("k")

    def test_real_keys_are_never_sent_to_the_local_emulator(self):
        local = storage._make_client("http://127.0.0.1:9000", "ap-southeast-1")
        self.assertEqual(local._request_signer._credentials.access_key, "local")


class FileReadingTests(SimpleTestCase):
    def test_type_comes_from_the_first_bytes(self):
        self.assertEqual(preprocess.sniff_type(picture(fmt="JPEG")), "image/jpeg")
        self.assertEqual(preprocess.sniff_type(picture(fmt="PNG")), "image/png")
        self.assertEqual(preprocess.sniff_type(b"%PDF-1.7 ..."), "application/pdf")
        for data in (b"", b"GIF89a", b"<svg>", b"MZ\x90\x00", picture(fmt="WEBP")):
            self.assertIsNone(preprocess.sniff_type(data), data[:8])

    def test_load_image(self):
        for fmt in ("JPEG", "PNG"):
            self.assertEqual(preprocess.load_image(picture(size=(200, 160), fmt=fmt)).shape, (160, 200, 3))
        pdf = io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(pdf, "PDF")
        self.assertEqual(preprocess.load_image(pdf.getvalue()).ndim, 3)
        big = preprocess.load_image(picture(size=(5000, 2500)))
        self.assertEqual(max(big.shape), preprocess.MAX_SIDE)

    def test_sideways_photo_is_turned_upright_from_its_camera_tag(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 degrees to view"
        out = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(out, "JPEG", exif=exif)
        self.assertEqual(preprocess.load_image(out.getvalue()).shape[:2], (200, 100))

    def test_damaged_files_raise_one_clear_error(self):
        for data in (b"plain text", b"\xff\xd8\xff damaged", b"\x89PNG\r\n\x1a\n damaged", b"%PDF-1.7 damaged"):
            with self.assertRaises(preprocess.UnreadableFile):
                preprocess.load_image(data)


class PreprocessTests(SimpleTestCase):
    def card_photo(self, tilt=0):
        """A white card with dark text lines on a dark table, optionally tilted."""
        scene = Image.new("RGB", (900, 700), (40, 40, 40))
        card = Image.new("RGB", (634, 400), "white")
        draw = ImageDraw.Draw(card)
        for y in range(60, 360, 50):
            draw.rectangle((40, y, 590, y + 14), fill=(20, 20, 20))
        card = card.rotate(tilt, expand=True, fillcolor=(40, 40, 40))
        scene.paste(card, ((900 - card.width) // 2, (700 - card.height) // 2))
        return cv2.cvtColor(np.asarray(scene), cv2.COLOR_RGB2BGR)

    def test_card_is_cropped_out_of_the_background(self):
        cropped = preprocess.crop_card(self.card_photo())
        height, width = cropped.shape[:2]
        self.assertTrue(600 < width < 670 and 370 < height < 430, cropped.shape)
        self.assertGreater(cropped.mean(), 150)  # mostly card now, not table

    def test_tilted_text_is_levelled(self):
        def tilt_of(image):
            edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 50, 150)
            found = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=180, maxLineGap=10)
            return float(np.median([np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in found.reshape(-1, 4)]))

        tilted = self.card_photo(tilt=6)
        self.assertGreater(abs(tilt_of(tilted)), 4)
        self.assertLess(abs(tilt_of(preprocess.deskew(tilted))), 1.5)

    def test_unsure_steps_leave_the_picture_alone(self):
        blank = np.full((300, 400, 3), 255, np.uint8)
        self.assertIs(preprocess.crop_card(blank), blank)
        self.assertIs(preprocess.deskew(blank), blank)
        noise = np.random.default_rng(1).integers(0, 255, (120, 160, 3), dtype=np.uint8)
        for image in (blank, noise, np.zeros((8, 8, 3), np.uint8)):
            self.assertEqual(preprocess.prepare_for_ocr(image).ndim, 3)

    def test_contrast_boost_and_sharpness(self):
        flat = np.full((200, 200, 3), 120, np.uint8)
        flat[80:120, 40:160] = 135  # faint print
        boosted = preprocess.boost_contrast(flat)
        self.assertGreater(float(boosted.max()) - float(boosted.min()), 15)
        sharp = self.card_photo()
        self.assertGreater(preprocess.sharpness(sharp), preprocess.sharpness(cv2.GaussianBlur(sharp, (21, 21), 0)) * 5)


class MappingTests(SimpleTestCase):
    def test_labels_on_the_same_line_or_the_line_above(self):
        found = values(mapping.map_fields(lines(
            "Name", "DELA CRUZ, JUAN SANTOS", "SR-Code: 21-12345", "Program", "BS Computer Science",
            "Year Level: 3rd Year", "Sex: M", "Date of Birth: May 17, 2004", "Address: 123 Rizal St, Batangas City",
            "College: College of Informatics and Computing Sciences", "Valid until: July 2027",
        ), TEMPLATE))
        self.assertEqual(found, {
            "last_name": "Dela Cruz", "first_name": "Juan", "middle_name": "Santos", "student_id_number": "21-12345",
            "program": "BS Computer Science", "year_level": "3", "sex": "male", "date_of_birth": "2004-05-17",
            "address": "123 Rizal St, Batangas City", "college": "College of Informatics and Computing Sciences",
            "id_valid_until": "July 2027",
        })

    def test_values_without_labels_are_found_by_their_look(self):
        extraction = mapping.map_fields(lines(
            "BATANGAS STATE UNIVERSITY", "The National Engineering University", "STUDENT",
            "REYES, ANA MARIE D.", "21-54321", "Bachelor of Science in Information Technology",
            "COLLEGE OF ENGINEERING", "2ND YEAR", "A.Y. 2026-2027", "juan@g.batstate-u.edu.ph",
        ), TEMPLATE)
        self.assertEqual(values(extraction), {
            "last_name": "Reyes", "first_name": "Ana", "middle_name": "Marie D", "student_id_number": "21-54321",
            "program": "Bachelor of Science in Information Technology", "college": "COLLEGE OF ENGINEERING",
            "year_level": "2", "id_valid_until": "A.Y. 2026-2027",
        })
        self.assertEqual((extraction.raw_name, extraction.name_format), ("REYES, ANA MARIE D.", "comma"))
        # A guess is trusted less than a value that followed its label.
        self.assertLess(extraction.fields["program"]["confidence"], 0.7)

    def test_surname_on_its_own_line(self):
        extraction = mapping.map_fields(lines("DELA CRUZ,", "JUAN SANTOS", "SR-CODE 21-12345"), TEMPLATE)
        self.assertEqual(extraction.raw_name, "DELA CRUZ, JUAN SANTOS")
        self.assertEqual(values(extraction)["last_name"], "Dela Cruz")

    def test_lost_comma_keeps_surname_first_but_is_marked(self):
        extraction = mapping.map_fields(lines("BATANGAS STATE UNIVERSITY", "DELA CRUZ JUAN SANTOS"), TEMPLATE)
        self.assertEqual(values(extraction), {"last_name": "Dela Cruz", "first_name": "Juan", "middle_name": "Santos"})
        self.assertTrue(extraction.name_low_confidence)

    def test_nothing_readable_gives_nothing(self):
        for text in ([], lines("BATANGAS STATE UNIVERSITY", "STUDENT"), lines("12345", "@@@", "Email: a@b.ph")):
            self.assertEqual(mapping.map_fields(text, TEMPLATE).fields, {})

    def test_dates(self):
        for text in ("05/17/2004", "05-17-2004", "2004-05-17", "May 17, 2004", "MAY 17 2004", "17 May 2004", "Sep. 3, 2004"):
            self.assertRegex(mapping.parse_date(text), r"^2004-0[59]-\d\d$", text)
        for text in ("", "13/40/2004", "tomorrow", "05/17/2999", "01/01/1800"):
            self.assertEqual(mapping.parse_date(text), "", text)


class LivenessTests(SimpleTestCase):
    def face(self, turn, tilt=0.0):
        right_eye, left_eye = (100.0, 100.0), (200.0, 100.0 + tilt)
        return faces.Face(box=(50, 50, 200, 200), right_eye=right_eye, left_eye=left_eye,
                          nose=(100 + (0.5 + turn) * 100, 150.0), score=1.0)

    def test_challenge_shape(self):
        seen = set()
        for _ in range(60):
            steps = liveness.new_challenge(2)
            self.assertEqual((len(steps), steps[0], steps[2]), (4, "center", "center"))
            self.assertTrue({steps[1], steps[3]} <= {"left", "right"})
            seen.add((steps[1], steps[3]))
        self.assertEqual(len(seen), 4)  # every combination turns up: the order can't be guessed
        self.assertEqual(liveness.new_challenge(1)[0], "center")

    def test_turn_follows_the_nose_and_ignores_a_tilted_head(self):
        self.assertAlmostEqual(liveness.turn(self.face(0.0)), 0.0)
        self.assertAlmostEqual(liveness.turn(self.face(0.2)), 0.2)    # nose towards the person's left
        self.assertAlmostEqual(liveness.turn(self.face(-0.2)), -0.2)
        self.assertLess(abs(liveness.turn(self.face(0.0, tilt=30.0)) - 0.0), 0.2)
        collapsed = faces.Face(box=(0, 0, 1, 1), right_eye=(5, 5), left_eye=(5, 5), nose=(5, 5), score=1.0)
        self.assertIsNone(liveness.turn(collapsed))

    def judge(self, steps, turns):
        return liveness.judge(steps, turns, settings.LIVENESS_TURN_MIN, settings.LIVENESS_CENTER_MAX)

    def test_real_head_movement_passes_and_picks_the_best_frames(self):
        steps = ["center", "left", "center", "right"]
        verdict = self.judge(steps, [[0.05, 0.01], [0.05, 0.22, 0.30], [0.02], [-0.10, -0.20]])
        self.assertEqual((verdict.passed, verdict.picks), (True, (1, 2, 0, 1)))

    def test_static_image_small_tilt_wrong_way_and_missing_steps_fail(self):
        steps = ["center", "left", "center", "right"]
        still = [[0.0, 0.0]] * 4
        flat_photo_tilted = [[0.0], [0.05], [0.0], [-0.05]]
        wrong_way = [[0.0], [-0.3], [0.0], [0.3]]
        never_straight = [[0.3], [0.3], [0.3], [-0.3]]
        missing = [[0.0], [0.3], [], [-0.3]]
        for turns in (still, flat_photo_tilted, wrong_way, never_straight, missing):
            self.assertFalse(self.judge(steps, turns).passed, turns)


class PortraitOrientationTests(SimpleTestCase):
    """A real detector "finds" a sideways face too, only less surely. The surest turn must win."""

    class TurnAwareFaces:
        name = "turn-aware"

        def detect(self, image):
            height, width = image.shape[:2]
            upright = bool(image[0, 0, 0] == 255)  # the marked corner is top-left again
            return [faces.Face(box=(10, 10, 80, 80), right_eye=(30, 40), left_eye=(70, 40), nose=(50, 60),
                               score=0.95 if upright else 0.87)]

    def test_the_turn_the_detector_is_surest_of_is_used(self):
        from .registration import _find_portrait

        upright = np.zeros((120, 200, 3), np.uint8)
        upright[0, 0] = 255
        for turns in range(4):
            scanned = preprocess.quarter_turns(upright, turns)  # as a sideways or upside-down scan
            image, face = _find_portrait(scanned, self.TurnAwareFaces())
            self.assertEqual((image.shape, int(image[0, 0, 0]), face.score), ((120, 200, 3), 255, 0.95), turns)

    def test_no_usable_portrait(self):
        from .registration import _find_portrait

        class NoFaces:
            def detect(self, image):
                return []

        image = np.zeros((120, 200, 3), np.uint8)
        self.assertEqual(_find_portrait(image, NoFaces()), (image, None))
        lying_down = faces.Face(box=(0, 0, 80, 80), right_eye=(40, 30), left_eye=(40, 70), nose=(60, 50), score=0.9)
        self.assertFalse(lying_down.upright)


class TemplateSealingTests(SimpleTestCase):
    def test_sealed_templates_need_the_key(self):
        vector = faces.unit(np.arange(1, 129, dtype=np.float32))
        blob = faces.seal(vector)
        self.assertNotIn(vector.tobytes(), blob)
        self.assertNotEqual(blob, faces.seal(vector))  # a fresh random value each time
        np.testing.assert_allclose(faces.unseal(blob), vector)
        np.testing.assert_allclose(faces.unseal(memoryview(blob)), vector)
        with self.settings(SECRET_KEY="another-secret"):
            self.assertIsNone(faces.unseal(blob))
        self.assertIsNone(faces.unseal(None))

    def test_similarity(self):
        a, b = faces.unit([1, 0, 0]), faces.unit([0, 1, 0])
        self.assertEqual((faces.similarity(a, a), faces.similarity(a, b)), (1.0, 0.0))
        self.assertEqual(faces.similarity(a, None), -1.0)
        self.assertEqual(faces.similarity(a, faces.unit([1, 0])), -1.0)  # templates from different engines


@skipUnless(MODELS_HERE, "face model files not downloaded (python manage.py fetch_face_models)")
class RealFaceEngineTests(SimpleTestCase):
    """The real OpenCV models on pictures with no face in them. Real faces need real photos: see the README."""

    def test_no_face_in_blank_or_text_only_pictures(self):
        provider = faces.OpenCVFaceProvider()
        self.assertEqual(provider.name, "opencv-sface-2021dec")
        blank = np.full((480, 640, 3), 255, np.uint8)
        self.assertEqual(provider.detect(blank), [])
        text = blank.copy()
        cv2.putText(text, "DELA CRUZ, JUAN", (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
        self.assertEqual(provider.detect(text), [])
