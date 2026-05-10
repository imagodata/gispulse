"""Unit tests for `core.slug`."""

from __future__ import annotations

import pytest

from core.slug import RESERVED, ensure_unique_slug, slugify


class TestSlugify:
    def test_basic_title(self) -> None:
        assert slugify("Carte des élections 2026") == "carte-des-elections-2026"

    def test_strips_diacritics(self) -> None:
        assert slugify("café") == "cafe"
        assert slugify("naïve façon") == "naive-facon"

    def test_collapses_punctuation_and_dashes(self) -> None:
        assert slugify("Hello,  world!! --- foo") == "hello-world-foo"

    def test_truncates_to_max_length(self) -> None:
        long = "a" * 200
        result = slugify(long, max_length=60)
        assert len(result) <= 60
        assert result == "a" * 60

    def test_truncation_strips_trailing_dash(self) -> None:
        assert slugify("hello-world-foo", max_length=11) == "hello-world"

    def test_empty_returns_random_token(self) -> None:
        result = slugify("")
        assert len(result) == 8
        assert result.isalnum()

    def test_pure_punctuation_returns_random_token(self) -> None:
        result = slugify("!!!---???")
        assert len(result) == 8
        assert result.isalnum()


class TestEnsureUniqueSlug:
    def test_returns_base_when_free(self) -> None:
        assert ensure_unique_slug("hello", exists=lambda _: False) == "hello"

    def test_salts_when_taken(self) -> None:
        result = ensure_unique_slug("hello", exists=lambda s: s == "hello")
        assert result.startswith("hello-")
        assert len(result) == len("hello-") + 4

    def test_salts_when_reserved(self) -> None:
        result = ensure_unique_slug("admin", exists=lambda _: False)
        assert result.startswith("admin-")
        assert "admin" in RESERVED

    def test_raises_after_max_attempts(self) -> None:
        with pytest.raises(RuntimeError, match="could not allocate"):
            ensure_unique_slug("hello", exists=lambda _: True, max_attempts=3)

    def test_avoids_reserved_in_salt_candidates(self) -> None:
        custom_reserved = frozenset({"hello", "hello-aaaa"})
        # First candidate "hello-aaaa" is reserved, must skip
        attempts = []

        def fake_exists(s: str) -> bool:
            attempts.append(s)
            return False

        result = ensure_unique_slug("hello", exists=fake_exists, reserved=custom_reserved)
        assert result not in custom_reserved
