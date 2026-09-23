import unittest

from tools.exa_context import (
    MAX_CONTEXT_QUERY_LENGTH,
    build_context_payload,
    normalize_tokens_num,
)


class ExaContextContractTests(unittest.TestCase):
    def test_default_tokens_num_is_dynamic(self):
        self.assertEqual(normalize_tokens_num(None), "dynamic")
        self.assertEqual(normalize_tokens_num(""), "dynamic")
        self.assertEqual(
            build_context_payload(" asyncio timeout ")["tokensNum"], "dynamic"
        )

    def test_numeric_tokens_num_is_normalized(self):
        self.assertEqual(normalize_tokens_num(5000), 5000)
        self.assertEqual(normalize_tokens_num("5000"), 5000)
        self.assertEqual(
            build_context_payload("query", tokens_num=1000)["tokensNum"], 1000
        )

    def test_invalid_tokens_num_is_rejected(self):
        for value in (49, 100001, "invalid", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "tokens_num"):
                    normalize_tokens_num(value)

    def test_query_is_required_and_limited(self):
        with self.assertRaisesRegex(ValueError, "query"):
            build_context_payload(" ")
        with self.assertRaisesRegex(ValueError, "query"):
            build_context_payload("x" * (MAX_CONTEXT_QUERY_LENGTH + 1))

    def test_payload_uses_api_field_names(self):
        payload = build_context_payload(
            "  React hooks state management  ", tokens_num="dynamic"
        )
        self.assertEqual(
            payload,
            {"query": "React hooks state management", "tokensNum": "dynamic"},
        )


if __name__ == "__main__":
    unittest.main()
