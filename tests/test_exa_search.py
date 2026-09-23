import unittest

from tools.exa_search import (
    SEARCH_CATEGORIES,
    SEARCH_TYPES,
    build_search_payload,
    normalize_category,
    normalize_search_type,
    resolve_timeout_seconds,
)


class ExaSearchContractTests(unittest.TestCase):
    def test_current_search_types(self):
        self.assertEqual(
            SEARCH_TYPES,
            frozenset(
                {
                    "instant",
                    "fast",
                    "auto",
                    "deep-lite",
                    "deep",
                    "deep-reasoning",
                }
            ),
        )

    def test_legacy_search_types_map_to_auto(self):
        self.assertEqual(normalize_search_type("keyword"), "auto")
        self.assertEqual(normalize_search_type("neural"), "auto")
        self.assertEqual(normalize_search_type("unknown"), "auto")

    def test_current_categories_and_legacy_alias(self):
        self.assertEqual(
            SEARCH_CATEGORIES,
            frozenset(
                {
                    "company",
                    "publication",
                    "news",
                    "personal site",
                    "financial report",
                    "people",
                }
            ),
        )
        self.assertEqual(normalize_category("research paper"), "publication")
        self.assertEqual(normalize_category("unknown"), "")

    def test_payload_uses_current_contract(self):
        payload = build_search_payload(
            "latest research",
            search_type="deep",
            category="research paper",
            include_domains="arxiv.org, example.com",
        )
        self.assertEqual(payload["type"], "deep")
        self.assertEqual(payload["category"], "publication")
        self.assertEqual(payload["includeDomains"], ["arxiv.org", "example.com"])

    def test_vertical_date_filters_are_rejected_before_request(self):
        for category in ("company", "people"):
            for field, value in (
                ("start_published_date", "2026-01-01T00:00:00Z"),
                ("end_published_date", "2026-12-31T00:00:00Z"),
            ):
                with self.subTest(category=category, field=field):
                    with self.assertRaisesRegex(ValueError, "不支持参数"):
                        build_search_payload(
                            "query", category=category, **{field: value}
                        )

    def test_people_exclude_domains_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "excludeDomains"):
            build_search_payload(
                "query", category="people", exclude_domains="example.com"
            )

    def test_company_exclude_domains_are_preserved(self):
        payload = build_search_payload(
            "query", category="company", exclude_domains="example.com"
        )
        self.assertEqual(payload["excludeDomains"], ["example.com"])

    def test_supported_vertical_filter_is_preserved(self):
        payload = build_search_payload(
            "query",
            category="company",
            include_domains="example.com",
        )
        self.assertEqual(payload["includeDomains"], ["example.com"])

    def test_deep_search_timeout_floor(self):
        self.assertEqual(resolve_timeout_seconds(30, "deep-lite"), 30)
        self.assertEqual(resolve_timeout_seconds(30, "deep"), 60)
        self.assertEqual(resolve_timeout_seconds(30, "deep-reasoning"), 90)
        self.assertEqual(resolve_timeout_seconds(120, "deep-reasoning"), 120)


if __name__ == "__main__":
    unittest.main()
