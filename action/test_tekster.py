import os
import unittest
from unittest import mock

import tekster


class TeksterTest(unittest.TestCase):
    def test_danish_is_the_default_without_a_language(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tekster.language(), "da")
            self.assertEqual(tekster.t("risk_low"), "🟢 Lav")

    def test_english_is_used_when_chosen(self):
        with mock.patch.dict(os.environ, {"MANILENS_LANGUAGE": "en"}):
            self.assertEqual(tekster.t("risk_low"), "🟢 Low")

    def test_an_unknown_language_falls_back_to_danish(self):
        for value in ("de", "", "Danish", "EN-GB", "  "):
            with mock.patch.dict(os.environ, {"MANILENS_LANGUAGE": value}):
                self.assertEqual(tekster.language(), "da", value)

    def test_the_code_is_read_case_insensitively(self):
        with mock.patch.dict(os.environ, {"MANILENS_LANGUAGE": "EN"}):
            self.assertEqual(tekster.language(), "en")

    def test_both_languages_carry_every_key(self):
        # En manglende nøgle ville give engelske kommentarer med huller i.
        self.assertEqual(set(tekster.DA), set(tekster.EN))

    def test_a_missing_key_falls_back_to_danish_rather_than_nothing(self):
        with mock.patch.dict(tekster.EN, clear=True), mock.patch.dict(os.environ, {"MANILENS_LANGUAGE": "en"}):
            self.assertEqual(tekster.t("risk_low"), "🟢 Lav")

    def test_placeholders_are_filled(self):
        self.assertEqual(
            tekster.t("review_counts", kritisk=1, alvorlig=2, mindre=3, fixed=4),
            "1 kritiske · 2 alvorlige · 3 mindre · 4 rettet siden sidst",
        )

    def test_every_danish_placeholder_exists_in_english(self):
        # Et felt, der kun findes på det ene sprog, ville kaste KeyError midt i et review.
        import re

        for key, danish in tekster.DA.items():
            fields = set(re.findall(r"\{(\w+)\}", danish))
            self.assertEqual(fields, set(re.findall(r"\{(\w+)\}", tekster.EN[key])), key)

    def test_the_schema_words_are_not_translated(self):
        # kritisk|alvorlig|mindre er wire-format mellem prompt, Python og websiden.
        self.assertIn("severity_kritisk", tekster.EN)
        self.assertIn("summary_tilfoejet", tekster.EN)




class SprogskifteTest(unittest.TestCase):
    """Et sprogskift må ikke gøre bottens egne, ældre kommentarer ulæselige for koden."""

    def test_a_minor_finding_is_recognised_in_both_languages(self):
        import post_review

        danish = "_\U0001f7e1 Mindre_ | _Korrekthed_\n\n**Noget**"
        english = "_\U0001f7e1 Minor_ | _Correctness_\n\n**Something**"
        for code in ("da", "en"):
            with mock.patch.dict(os.environ, {"MANILENS_LANGUAGE": code}):
                self.assertTrue(post_review.is_minor_finding_comment(danish), code)
                self.assertTrue(post_review.is_minor_finding_comment(english), code)

    def test_a_blocking_finding_is_not_mistaken_for_a_minor_one(self):
        import post_review

        for body in ("_\U0001f534 Kritisk_ | _Korrekthed_", "_\U0001f7e0 Major_ | _Correctness_", "", "noget helt andet"):
            self.assertFalse(post_review.is_minor_finding_comment(body), body)

if __name__ == "__main__":
    unittest.main()
