"""GeoJSON normalisation and property helpers.

Pure-Python utilities — no spatial library dependency. Functions operate on
plain dicts and Python primitives so they can be used anywhere in the stack
without pulling in GeoPandas or Shapely.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

#: A (lon, lat) position as a plain float tuple.
Position = tuple[float, float]

_GEOMETRY_TYPES = frozenset(
    {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
    }
)

# GeometryCollection has a ``geometries`` array rather than ``coordinates``.
_GEOMETRY_COLLECTION_TYPE = "GeometryCollection"


def first_str(props: Mapping[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string value found among *keys* in *props*.

    Args:
        props: A mapping of property names to values (e.g. a GeoJSON
            ``feature["properties"]`` dict).
        *keys: Candidate keys to probe in order.

    Returns:
        The string value of the first key whose value is neither ``None``
        nor the empty string, or ``None`` if no such key exists.
    """
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def iter_positions(value: Any) -> Iterator[Position]:
    """Recursively yield ``(lon, lat)`` position tuples from a GeoJSON coordinate tree.

    Handles all GeoJSON coordinate shapes (single position, rings,
    multi-part geometries) by walking nested lists and tuples.

    Args:
        value: A GeoJSON ``coordinates`` value at any nesting level.

    Yields:
        ``(float, float)`` position tuples.
    """
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield (float(value[0]), float(value[1]))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_positions(item)


def bbox_for_geometry(geometry: Mapping[str, Any] | None) -> list[float] | None:
    """Compute the bounding box of a GeoJSON geometry dict.

    Supports all seven GeoJSON geometry types including ``GeometryCollection``.

    Args:
        geometry: A GeoJSON geometry object (``{"type": ..., "coordinates": ...}``
            or ``{"type": "GeometryCollection", "geometries": [...]}``) or ``None``.

    Returns:
        ``[minlon, minlat, maxlon, maxlat]``, or ``None`` if the geometry is
        ``None`` or contains no positions.
    """
    if not geometry:
        return None
    if geometry.get("type") == _GEOMETRY_COLLECTION_TYPE:
        all_positions: list[Position] = []
        for member in geometry.get("geometries") or []:
            sub = bbox_for_geometry(member)
            if sub is not None:
                all_positions.append((sub[0], sub[1]))
                all_positions.append((sub[2], sub[3]))
        if not all_positions:
            return None
        xs = [p[0] for p in all_positions]
        ys = [p[1] for p in all_positions]
        return [min(xs), min(ys), max(xs), max(ys)]
    positions = list(iter_positions(geometry.get("coordinates")))
    if not positions:
        return None
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    return [min(xs), min(ys), max(xs), max(ys)]


def _json_coordinates(value: Any) -> Any:
    """Recursively convert tuple-backed coordinate trees to plain lists."""
    if isinstance(value, tuple):
        return [_json_coordinates(item) for item in value]
    if isinstance(value, list):
        return [_json_coordinates(item) for item in value]
    return value


def normalize_geometry(value: Any) -> dict[str, Any] | None:
    """Validate and normalise a GeoJSON geometry dict.

    Supports all seven GeoJSON geometry types.  ``GeometryCollection`` members
    are recursively normalised; any invalid member is dropped silently.
    Rejects non-Mapping inputs, unknown geometry types, missing or non-list
    coordinates, and geometries with no extractable positions.
    Tuple-backed coordinate arrays are converted to plain lists so the result
    is always JSON-serialisable.

    Args:
        value: Candidate GeoJSON geometry — typically ``feature["geometry"]``.

    Returns:
        A normalised geometry dict, or ``None`` if *value* is invalid.
    """
    if not isinstance(value, Mapping):
        return None
    geometry_type = value.get("type")

    if geometry_type == _GEOMETRY_COLLECTION_TYPE:
        raw_members = value.get("geometries")
        if not isinstance(raw_members, (list, tuple)):
            return None
        members = [normalize_geometry(m) for m in raw_members]
        valid_members = [m for m in members if m is not None]
        # An empty but well-formed GeometryCollection is valid GeoJSON.
        return {"type": _GEOMETRY_COLLECTION_TYPE, "geometries": valid_members}

    if geometry_type not in _GEOMETRY_TYPES:
        return None
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, (list, tuple)):
        return None
    normalized_coordinates = _json_coordinates(coordinates)
    if not list(iter_positions(normalized_coordinates)):
        return None
    return {"type": str(geometry_type), "coordinates": normalized_coordinates}


def _is_missing(value: Any) -> bool:
    """Return ``True`` for NaN-like values (``value != value`` idiom)."""
    try:
        return bool(value != value)
    except Exception:
        return False


def json_safe(value: Any) -> Any:
    """Convert *value* to a JSON-serialisable form.

    Handles the common cases found in geospatial pipelines:

    * ``None`` and NaN-like values → ``None``
    * Objects with ``.isoformat()`` (``datetime``, ``date``) → ISO 8601 string
    * NumPy scalars with ``.item()`` → unwrapped Python scalar
    * Anything else that is not JSON-serialisable → ``str(value)``

    Args:
        value: Any Python value.

    Returns:
        A value that ``json.dumps`` can serialise without raising.
    """
    if value is None or _is_missing(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        # ValueError is raised by json.dumps on circular structures.
        return str(value)
