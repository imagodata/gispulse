"""URL-safe slug generation for Cocarte maps and other public artefacts.

Generates kebab-case slugs from arbitrary titles, strips diacritics,
enforces a configurable max length, dedupes against an existence
check, and refuses reserved top-level route segments.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable
from string import ascii_lowercase, digits

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH = re.compile(r"-{2,}")

# Reserved top-level path segments. A slug equal to any of these would
# collide with a route prefix and must be salted.
RESERVED: frozenset[str] = frozenset(
    {
        "api",
        "admin",
        "static",
        "ws",
        "health",
        "auth",
        "login",
        "logout",
        "c",
        "embed",
        "share",
        "cocarte",
        "settings",
        "docs",
        "marketplace",
        "_next",
        "assets",
        "manifest.json",
        "robots.txt",
    }
)


def _strip_diacritics(s: str) -> str:
    """Decompose Unicode then drop combining marks (NFKD)."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _random_token(n: int) -> str:
    """Cryptographically random lowercase alphanumeric of length n."""
    return "".join(secrets.choice(ascii_lowercase + digits) for _ in range(n))


def slugify(title: str, *, max_length: int = 60) -> str:
    """Convert *title* to a URL-safe kebab-case slug.

    Empty or pure-punctuation inputs return an 8-char random token rather
    than an empty string so callers always receive a usable slug.
    """
    s = _strip_diacritics(title).lower().strip()
    s = _SLUG_RE.sub("-", s)
    s = _MULTI_DASH.sub("-", s).strip("-")
    s = s[:max_length].strip("-")
    return s or _random_token(8)


def ensure_unique_slug(
    base: str,
    *,
    exists: Callable[[str], bool],
    reserved: frozenset[str] = RESERVED,
    max_attempts: int = 10,
) -> str:
    """Return *base* if available and not reserved; otherwise salt with a
    short random suffix until a free slug is found.

    Raises RuntimeError after `max_attempts` collisions.
    """
    if base not in reserved and not exists(base):
        return base
    for _ in range(max_attempts):
        candidate = f"{base}-{_random_token(4)}"
        if candidate not in reserved and not exists(candidate):
            return candidate
    raise RuntimeError(f"could not allocate unique slug for {base!r} after {max_attempts} attempts")
