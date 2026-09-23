import unittest

from tools.exa_commands import (
    extract_exa_payload,
    is_reserved_exa_query,
    parse_clean_query,
    parse_research_query,
    parse_search_term,
    parse_stats_query,
)


class ExaCommandParsingTests(unittest.TestCase):
    def test_research_query_preserves_inner_spaces(self):
        self.assertEqual(
            parse_research_query("-r  compare   a b c  "),
            "compare   a b c",
        )

    def test_research_query_requires_content(self):
        with self.assertRaisesRegex(ValueError, "研究问题"):
            parse_research_query("-r")

    def test_payload_extraction_and_search_term_preserve_spaces(self):
        payload = extract_exa_payload("exa -r  compare   a b c  ")
        self.assertEqual(parse_research_query(payload), "compare   a b c")
        self.assertEqual(parse_search_term("-s  FastAPI   security"), "FastAPI   security")
        self.assertEqual(extract_exa_payload("/exa stats -q r-1"), "stats -q r-1")
    def test_stats_query_supports_optional_quiet_export(self):
        self.assertEqual(parse_stats_query("r-20260924-0001"), (False, "r-20260924-0001"))
        self.assertEqual(parse_stats_query("-q r-20260924-0001"), (True, "r-20260924-0001"))
        with self.assertRaisesRegex(ValueError, "任务号"):
            parse_stats_query("-q")

    def test_clean_query_supports_all_and_task_id(self):
        self.assertEqual(parse_clean_query("all"), "all")
        self.assertEqual(parse_clean_query("r-20260924-0001"), "r-20260924-0001")
        with self.assertRaisesRegex(ValueError, "任务号"):
            parse_clean_query("all now")

    def test_admin_prefixes_are_not_public_searches(self):
        for query in (
            "-r question",
            "-s",
            "stats",
            "stats report",
            "stats -q r-1",
            "clean all",
            "cancel r-1",
        ):
            with self.subTest(query=query):
                self.assertTrue(is_reserved_exa_query(query))

    def test_ordinary_searches_remain_public(self):
        for query in ("1 2 3", "compare a b c", "-research", "cleaning"):
            with self.subTest(query=query):
                self.assertFalse(is_reserved_exa_query(query))


if __name__ == "__main__":
    unittest.main()
