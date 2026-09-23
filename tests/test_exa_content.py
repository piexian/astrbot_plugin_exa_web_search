import unittest

from tools.exa_content import (
    build_contents_payload,
    normalize_max_age_hours,
)


class ExaContentContractTests(unittest.TestCase):
    def test_default_payload_omits_freshness(self):
        payload = build_contents_payload("https://example.com")
        self.assertEqual(
            payload,
            {
                "ids": ["https://example.com"],
                "text": {"maxCharacters": 3000},
            },
        )

    def test_freshness_values_are_normalized(self):
        self.assertEqual(normalize_max_age_hours(None), None)
        self.assertEqual(normalize_max_age_hours(0), 0)
        self.assertEqual(normalize_max_age_hours("24"), 24)
        self.assertEqual(
            build_contents_payload("https://example.com", max_age_hours=0)[
                "maxAgeHours"
            ],
            0,
        )

    def test_invalid_freshness_is_rejected(self):
        for value in (-2, 721, True, "invalid"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_age_hours"):
                    normalize_max_age_hours(value)


if __name__ == "__main__":
    unittest.main()
