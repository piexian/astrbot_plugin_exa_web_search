import unittest

from tools.exa_response import normalize_cost_total


class ExaResponseNormalizationTests(unittest.TestCase):
    def test_object_cost_uses_total(self):
        self.assertEqual(normalize_cost_total({"total": 1.25}), 1.25)
        self.assertEqual(normalize_cost_total({}), "N/A")

    def test_scalar_zero_cost_is_preserved(self):
        self.assertEqual(normalize_cost_total(0), 0)
        self.assertEqual(normalize_cost_total("0"), "0")

    def test_scalar_and_missing_cost_are_supported(self):
        self.assertEqual(normalize_cost_total("1.5"), "1.5")
        self.assertEqual(normalize_cost_total(None), "N/A")


if __name__ == "__main__":
    unittest.main()
