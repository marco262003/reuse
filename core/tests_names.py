"""Run with:  python manage.py test core.tests_names   (pure functions, no database)"""
from django.test import SimpleTestCase

from . import names, registration_fields as rf
from .names import COMMA, NO_COMMA, parse_name


class CommaFormatTests(SimpleTestCase):
    def test_surname_then_given_names(self):
        name = parse_name("DELA CRUZ, JUAN SANTOS")
        self.assertEqual(name.format, COMMA)
        self.assertEqual(name.parts, {"last_name": "Dela Cruz", "first_name": "Juan", "middle_name": "Santos"})
        self.assertFalse(name.low_confidence)

    def test_missing_middle_name_is_left_out(self):
        name = parse_name("REYES, ANA")
        self.assertEqual(name.parts, {"last_name": "Reyes", "first_name": "Ana"})
        self.assertNotIn("middle_name", name.confidence)

    def test_middle_initial(self):
        self.assertEqual(parse_name("CRUZ, JUAN D.").parts["middle_name"], "D")

    def test_extra_given_names_go_to_middle_name_with_less_confidence(self):
        name = parse_name("REYES, JUAN CARLOS SANTOS")
        self.assertEqual((name.parts["first_name"], name.parts["middle_name"]), ("Juan", "Carlos Santos"))
        self.assertLess(name.confidence["middle_name"], name.confidence["last_name"])

    def test_suffix_in_any_position(self):
        for raw in ("DELA CRUZ, JUAN SANTOS JR.", "DELA CRUZ JR., JUAN SANTOS", "DELA CRUZ, JUAN JR. SANTOS",
                    "DELA CRUZ JR, JUAN SANTOS"):
            name = parse_name(raw)
            self.assertEqual(name.parts, {"last_name": "Dela Cruz", "first_name": "Juan",
                                          "middle_name": "Santos", "suffix": "Jr"}, raw)

    def test_roman_numeral_suffixes(self):
        self.assertEqual(parse_name("SANTOS, PEDRO III").parts["suffix"], "III")
        self.assertEqual(parse_name("SANTOS, PEDRO REYES IV").parts["suffix"], "IV")
        self.assertEqual(parse_name("SANTOS SR., PEDRO").parts["suffix"], "Sr")

    def test_v_after_a_comma_is_a_middle_initial(self):
        self.assertEqual(parse_name("CRUZ, JUAN V").parts, {"last_name": "Cruz", "first_name": "Juan", "middle_name": "V"})

    def test_compound_surnames_stay_whole(self):
        self.assertEqual(parse_name("DE LA SALLE, MARIA").parts["last_name"], "De La Salle")
        self.assertEqual(parse_name("VAN DER BERG, ANNA").parts["last_name"], "Van Der Berg")


class NoCommaFormatTests(SimpleTestCase):
    def test_first_middle_last_is_a_low_confidence_guess(self):
        name = parse_name("JUAN SANTOS REYES")
        self.assertEqual(name.format, NO_COMMA)
        self.assertEqual(name.parts, {"first_name": "Juan", "middle_name": "Santos", "last_name": "Reyes"})
        self.assertTrue(name.low_confidence)
        self.assertTrue(all(score < names.LOW_CONFIDENCE for score in name.confidence.values()))

    def test_suffix_and_compound_surname(self):
        self.assertEqual(parse_name("JUAN DELA CRUZ JR").parts, {"first_name": "Juan", "last_name": "Dela Cruz", "suffix": "Jr"})
        self.assertEqual(parse_name("Juan Dela Cruz, Jr.").parts, {"first_name": "Juan", "last_name": "Dela Cruz", "suffix": "Jr"})

    def test_compound_surnames_stay_whole(self):
        self.assertEqual(parse_name("MARIA CLARA DE LA SALLE").parts,
                         {"first_name": "Maria", "middle_name": "Clara", "last_name": "De La Salle"})
        self.assertEqual(parse_name("ANNA VAN DER BERG").parts, {"first_name": "Anna", "last_name": "Van Der Berg"})

    def test_middle_initial(self):
        self.assertEqual(parse_name("JUAN D. CRUZ").parts, {"first_name": "Juan", "middle_name": "D", "last_name": "Cruz"})

    def test_two_words_have_no_middle_name(self):
        self.assertEqual(parse_name("ANA REYES").parts, {"first_name": "Ana", "last_name": "Reyes"})

    def test_comma_template_with_a_lost_comma_keeps_the_surname_first(self):
        name = parse_name("DELA CRUZ JUAN SANTOS", name_format=COMMA)
        self.assertEqual(name.parts, {"last_name": "Dela Cruz", "first_name": "Juan", "middle_name": "Santos"})
        self.assertEqual(name.format, NO_COMMA)
        self.assertTrue(name.low_confidence)


class CleaningTests(SimpleTestCase):
    def test_placeholders_are_empty(self):
        for middle in ("N/A", "NA", "NONE", "-", ".", "|"):
            self.assertEqual(parse_name(f"CRUZ, JUAN {middle}").parts, {"last_name": "Cruz", "first_name": "Juan"}, middle)

    def test_nothing_readable_gives_no_parts(self):
        for raw in ("", None, "N/A", "--", "12345"):
            name = parse_name(raw)
            self.assertEqual(name.parts, {}, raw)
            self.assertFalse(name.low_confidence)

    def test_no_part_is_ever_an_empty_string(self):
        for raw in ("CRUZ,", ", JUAN", "CRUZ", "CRUZ, JUAN", "JUAN CRUZ JR"):
            for value in parse_name(raw).parts.values():
                self.assertTrue(value.strip(), raw)

    def test_casing(self):
        self.assertEqual(parse_name("O'BRIEN-SANTOS, NIÑO").parts, {"last_name": "O'Brien-Santos", "first_name": "Niño"})
        self.assertEqual(parse_name("MCDONALD, ANN").parts["last_name"], "McDonald")
        self.assertEqual(parse_name("AHMAD BIN ALI").parts, {"first_name": "Ahmad", "last_name": "Bin Ali"})
        self.assertEqual(parse_name("ALI, AHMAD FARID BIN OMAR").parts["middle_name"], "Farid bin Omar")
        # Mixed case is what the card says: it is not "corrected".
        self.assertEqual(parse_name("dela Cruz, Juan").parts["last_name"], "dela Cruz")

    def test_raw_string_is_kept_and_confidence_follows_ocr(self):
        name = parse_name("  CRUZ,   JUAN ", ocr_confidence=0.8)
        self.assertEqual(name.raw, "  CRUZ,   JUAN ")
        self.assertEqual(name.confidence, {"last_name": 0.8, "first_name": 0.8})


class TypedValueTests(SimpleTestCase):
    def test_name_part_rules(self):
        for good in ("Juan", "Dela Cruz", "O'Brien", "Santos-Reyes", "Niño", "D"):
            self.assertIsNone(names.check_name_part(good), good)
        for bad in ("Juan2", "N/A", "NA", "none", "-", "...", "Ma. Cristina", "x" * 61):
            self.assertIsNotNone(names.check_name_part(bad), bad)
        self.assertEqual(names.check_name_part(""), names.REQUIRED)
        self.assertIsNone(names.check_name_part("", required=False))

    def test_middle_name_never_becomes_null_text(self):
        for value in ("null", "undefined", "NULL", "", None, "   "):
            self.assertEqual(names.clean_middle_name(value), "")
        self.assertEqual(names.clean_middle_name("  De   Leon "), "De Leon")

    def test_suffix_rules(self):
        self.assertEqual([names.clean_suffix(s) for s in ("jr.", "SR", "iii", "v", "Esq")], ["Jr", "Sr", "III", "V", "Esq"])
        for good in ("", "Jr", "V", "Esq", "2nd"):
            self.assertIsNone(names.check_suffix(good), good)
        for bad in ("N/A", "-", "ElevenChars", "<b>"):
            self.assertIsNotNone(names.check_suffix(bad), bad)


class FieldClassificationTests(SimpleTestCase):
    def test_every_field_is_in_exactly_one_category(self):
        self.assertEqual(rf.ID_DERIVED | rf.MANUAL_ONLY, set(rf.BY_NAME))
        self.assertFalse(rf.ID_DERIVED & rf.MANUAL_ONLY)
        self.assertTrue({"email", "mobile", "user_type", "password", "confirm_password", "consent"} <= rf.MANUAL_ONLY)
        self.assertTrue({"first_name", "middle_name", "last_name", "suffix", "student_id_number"} <= rf.ID_DERIVED)

    def test_a_field_is_manual_unless_declared_id_derived(self):
        self.assertEqual(rf.Field("nickname", "Nickname").source, rf.MANUAL)

    def test_filter_drops_manual_fields_and_empty_values(self):
        extracted = {
            "first_name": {"value": "Juan", "confidence": 0.9}, "last_name": "Cruz",
            "middle_name": {"value": "", "confidence": 0.9}, "suffix": "N/A", "program": "null", "college": None,
            "student_id_number": "21-12345", "date_of_birth": "2004-05-17",
            # Printed on some cards, but the person must type these themselves:
            "email": "juan@g.batstate-u.edu.ph", "mobile": "+639171234567", "user_type": "student",
            "password": "x", "consent": True, "not_a_field": "x",
        }
        self.assertEqual(set(rf.only_id_derived(extracted)),
                         {"first_name", "last_name", "student_id_number", "date_of_birth"})

    def test_password_fields_are_never_prefilled_by_the_browser(self):
        for name in ("password", "confirm_password"):
            self.assertEqual(rf.BY_NAME[name].autocomplete, "new-password")

    def test_required_depends_on_user_type(self):
        self.assertTrue(rf.is_required(rf.BY_NAME["program"], "student"))
        self.assertFalse(rf.is_required(rf.BY_NAME["program"], "faculty"))
        self.assertTrue(rf.is_required(rf.BY_NAME["last_name"], "staff"))
        self.assertFalse(rf.is_required(rf.BY_NAME["middle_name"], "student"))
