"""Road-routing providers — distance + trace between two points.

Ported from the routing layer of an internal fibre-network project (its
ADR-01 ``RoutingProvider`` abstraction), genericized: no hardcoded CRS (the
source was welded to Belgian Lambert 72), no business config — every knob is
a constructor parameter.

The model: a provider turns two points (in a caller-chosen **metric** CRS)
into a distance in meters and optionally a polyline trace. Three
implementations, selectable at run time:

* :class:`TortuosityProvider` — instant, offline: distance = straight-line ×
  a calibrated detour ("tortuosity") factor per distance band. No real trace
  (returns the straight segment). The reference estimator and the fallback
  when a road router is unavailable.
* :class:`OSRMProvider` — real road routing against an OSRM instance
  (``/route`` per pair + ``/table`` batch matrices), over ``httpx``. Includes
  the degenerate-drop flooring: when OSRM snaps both endpoints of a short
  hop onto the same road node it returns ``distance=0`` for distinct points;
  those are floored to the tortuosity estimate instead of poisoning
  downstream cost models, and counted in a thread-safe
  :class:`OSRMDropTelemetry` so a high floor rate is visible (it signals a
  snapping problem, not noise).
* :class:`CachedRoutingProvider` — serves pre-routed pairs from a GeoParquet
  cache keyed by canonical geometric keys (:func:`route_key`); a missing
  pair raises ``ROUTE_CACHE_MISS`` and the caller decides the fallback.

Errors are machine-readable (:class:`RoutingError` with ``code`` +
``context``), never prose-only.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

Point = tuple[float, float]  # (x, y) in a metric CRS


# --------------------------------------------------------------------------- #
# Pure distance helpers                                                        #
# --------------------------------------------------------------------------- #
def euclidean_length(path: Sequence[Point]) -> float:
    """Euclidean length (meters) of a polyline in metric coordinates."""
    total = 0.0
    for (x0, y0), (x1, y1) in pairwise(path):
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def straight_distance(a: Point, b: Point) -> float:
    """Straight-line distance (meters) between two metric points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


@dataclass(frozen=True)
class TortuosityBand:
    """One detour-factor band: applies up to ``max_straight_m`` (inclusive).

    ``max_straight_m=None`` is the catch-all band for larger distances.
    Bands are evaluated in order; the first covering band wins.
    """

    max_straight_m: float | None
    factor: float


def tortuosity_factor(straight_m: float, bands: Sequence[TortuosityBand]) -> float:
    """Detour factor for a straight-line distance, from ordered bands.

    Raises:
        ValueError: negative distance, or no band covers the value (make the
            last band ``max_straight_m=None`` to catch everything).
    """
    if straight_m < 0:
        raise ValueError(f"negative straight-line distance: {straight_m}")
    for band in bands:
        if band.max_straight_m is None or straight_m <= band.max_straight_m:
            return band.factor
    raise ValueError(f"no tortuosity band covers distance {straight_m} m")


def tortuous_distance(straight_m: float, bands: Sequence[TortuosityBand]) -> float:
    """Estimated real distance = straight-line × tortuosity factor."""
    return straight_m * tortuosity_factor(straight_m, bands)


# --------------------------------------------------------------------------- #
# Provider abstraction                                                         #
# --------------------------------------------------------------------------- #
class RoutingError(RuntimeError):
    """A routing failure with a machine-readable ``code`` + ``context``.

    Codes let callers decide a fallback (e.g. drop to tortuosity when OSRM is
    unreachable) without parsing prose.
    """

    def __init__(self, code: str, context: str) -> None:
        super().__init__(f"{code}: {context}")
        self.code = code
        self.context = context


@dataclass(frozen=True)
class Route:
    """A routed pair: distance in meters + the polyline trace followed.

    ``geometry`` may be reduced to the straight segment ``(a, b)`` when the
    provider has no real trace (tortuosity estimate).
    """

    distance_m: float
    geometry: tuple[Point, ...] = field(default_factory=tuple)


@runtime_checkable
class RoutingProvider(Protocol):
    """Distance + trace between two points of a metric CRS.

    Narrow contract: implementations transform points into distance/trace,
    nothing more — no file or feature reads at query time.
    """

    def distance(self, a: Point, b: Point) -> float:
        """Routed distance in meters between ``a`` and ``b``."""
        ...

    def route(self, a: Point, b: Point) -> Route:
        """Full route (distance + geometry) between ``a`` and ``b``."""
        ...


@runtime_checkable
class BatchRoutingProvider(Protocol):
    """Optional capability: distances in bulk (sources × destinations matrix).

    Cell ``[i][j]`` is the distance from ``sources[i]`` to
    ``destinations[j]``; ``None`` marks an unroutable pair — the caller
    decides the fallback, the provider does not raise for it. Providers whose
    per-pair ``route`` is already O(1)-local (tortuosity, cache) gain nothing
    from batching and simply do not implement this protocol.
    """

    def distance_matrix(
        self,
        sources: Sequence[Point],
        destinations: Sequence[Point],
    ) -> list[list[float | None]]:
        """Distance matrix in meters, rows = sources, columns = destinations."""
        ...


@dataclass(frozen=True)
class TortuosityProvider:
    """Instant provider: distance = straight-line × calibrated detour factor.

    Produces no real trace: ``route`` returns the straight segment as
    geometry. The reference before a road router is available, and the
    fallback when one is not.
    """

    bands: tuple[TortuosityBand, ...]

    def distance(self, a: Point, b: Point) -> float:
        return tortuous_distance(straight_distance(a, b), self.bands)

    def route(self, a: Point, b: Point) -> Route:
        return Route(distance_m=self.distance(a, b), geometry=(a, b))


# --------------------------------------------------------------------------- #
# OSRM                                                                         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OSRMDropTelemetrySnapshot:
    """Frozen, serializable view of the degenerate-drop counters.

    ``floor_rate`` is ``floored_drop_count / routed_count`` (0.0 when nothing
    was routed): a high rate signals an OSRM snapping problem, not noise.
    ``floor_rate_suspect`` is set against a caller-supplied threshold;
    ``None`` = no threshold configured (never judged suspect).
    """

    routed_count: int
    floored_drop_count: int
    degenerate_drop_count: int
    floor_rate: float
    floor_rate_suspect: bool | None

    def as_dict(self) -> dict[str, object]:
        """Serializable dict with stable keys."""
        return {
            "routed_count": self.routed_count,
            "floored_drop_count": self.floored_drop_count,
            "degenerate_drop_count": self.degenerate_drop_count,
            "floor_rate": self.floor_rate,
            "floor_rate_suspect": self.floor_rate_suspect,
        }


@dataclass
class OSRMDropTelemetry:
    """Thread-safe counters for OSRM degenerate drops.

    Mutated during routing (the provider may be driven from a thread pool).
    Distinguishes two cases:

    - ``floored_drop_count``: "noise" drop floored to the tortuosity fallback
      (OSRM distance ≈ 0 between *distinct* points — endpoints snapped onto
      the same road node);
    - ``degenerate_drop_count``: unfloorable degenerate (coincident input
      points, or no fallback bands) — a high count here points at the
      upstream data, not at snapping.
    """

    routed_count: int = 0
    floored_drop_count: int = 0
    degenerate_drop_count: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def record_routed(self) -> None:
        with self._lock:
            self.routed_count += 1

    def record_floored_drop(self) -> None:
        with self._lock:
            self.floored_drop_count += 1

    def record_degenerate_drop(self) -> None:
        with self._lock:
            self.degenerate_drop_count += 1

    def floor_rate(self) -> float:
        """Share of observed OSRM distances that had to be floored (0.0 if none)."""
        with self._lock:
            if self.routed_count <= 0:
                return 0.0
            return self.floored_drop_count / self.routed_count

    def snapshot(
        self, *, suspect_floor_rate: float | None = None
    ) -> OSRMDropTelemetrySnapshot:
        """Frozen view, optionally flagged against a suspect-rate threshold."""
        with self._lock:
            routed = self.routed_count
            floored = self.floored_drop_count
            degenerate = self.degenerate_drop_count
        rate = floored / routed if routed > 0 else 0.0
        suspect: bool | None = (
            None if suspect_floor_rate is None else rate > suspect_floor_rate
        )
        return OSRMDropTelemetrySnapshot(
            routed_count=routed,
            floored_drop_count=floored,
            degenerate_drop_count=degenerate,
            floor_rate=rate,
            floor_rate_suspect=suspect,
        )


@dataclass
class OSRMProvider:
    """Real road routing against an OSRM instance, in a caller-chosen CRS.

    Input/output points are in ``crs`` (any pyproj-resolvable CRS; use a
    metric one so distances read in meters). They are reprojected to WGS84
    for the HTTP calls and the returned GeoJSON traces are reprojected back.
    Every failure (HTTP, no route, bad payload) raises :class:`RoutingError`
    with a machine-readable code.

    ``floor_bands`` handles the degenerate-drop case: when OSRM snaps both
    endpoints of a pair onto the same road node it answers ``distance=0``
    even for distinct points; with bands configured, such a pair is floored
    to the tortuosity estimate between the *original* points (consistent
    with a :class:`TortuosityProvider` fallback built from the same bands),
    and counted in ``telemetry``. ``degenerate_epsilon_m`` widens the
    "effectively zero" test (``None`` = strict ``<= 0``).
    """

    endpoint: str
    crs: str
    profile: str = "driving"
    timeout_s: float = 10.0
    table_batch_size: int | None = None
    floor_bands: tuple[TortuosityBand, ...] = ()
    degenerate_epsilon_m: float | None = None
    telemetry: OSRMDropTelemetry = field(
        default_factory=OSRMDropTelemetry, repr=False, compare=False
    )
    _thread_local: threading.local = field(
        default_factory=threading.local, init=False, repr=False, compare=False
    )
    _to_wgs84: Any = field(default=None, init=False, repr=False, compare=False)
    _to_crs: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        from pyproj import Transformer

        self._to_wgs84 = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        self._to_crs = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    # --- plumbing ---------------------------------------------------------- #
    def _client(self) -> Any:
        """One ``httpx.Client`` per thread (connection reuse, no sharing)."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a core dep
            raise ImportError("OSRMProvider requires 'httpx'.") from exc
        client = getattr(self._thread_local, "client", None)
        if client is None:
            client = httpx.Client(timeout=self.timeout_s)
            self._thread_local.client = client
        return client

    def _coords(self, points: Sequence[Point]) -> str:
        """``lon,lat;lon,lat;...`` in WGS84, the order OSRM expects."""
        wgs = (self._to_wgs84.transform(x, y) for x, y in points)
        return ";".join(f"{lon:.7f},{lat:.7f}" for lon, lat in wgs)

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        import httpx

        try:
            resp = self._client().get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RoutingError("OSRM_UNREACHABLE", f"{url}: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RoutingError("OSRM_BAD_PAYLOAD", f"non-JSON response: {exc}") from exc
        if not isinstance(payload, dict):
            raise RoutingError("OSRM_BAD_PAYLOAD", "non-object JSON response")
        return payload

    def _floor_degenerate(self, a: Point, b: Point, distance_m: float) -> float:
        """Floor a degenerate OSRM drop (distance ≈ 0) to the tortuosity fallback.

        Above the "effectively zero" threshold the OSRM distance is kept as
        is. Below it, the pair is floored to the tortuosity estimate between
        the **original** points — never zero for distinct points. Coincident
        input points, or no ``floor_bands``, cannot be floored: that is an
        upstream data problem, raised rather than masked.
        """
        self.telemetry.record_routed()
        threshold = (
            0.0 if self.degenerate_epsilon_m is None else self.degenerate_epsilon_m
        )
        if distance_m > threshold:
            return distance_m
        if not self.floor_bands:
            self.telemetry.record_degenerate_drop()
            raise RoutingError(
                "OSRM_DEGENERATE_DROP",
                f"distance={distance_m} <= {threshold} between {a} and {b} "
                "with no floor_bands configured",
            )
        straight = straight_distance(a, b)
        if straight <= 0.0:
            self.telemetry.record_degenerate_drop()
            raise RoutingError(
                "OSRM_DEGENERATE_DROP",
                f"coincident input points ({a}): zero-length pair",
            )
        self.telemetry.record_floored_drop()
        return tortuous_distance(straight, self.floor_bands)

    # --- RoutingProvider --------------------------------------------------- #
    def health_check(self, a: Point, b: Point) -> None:
        """Lightweight preflight on a caller-supplied probe pair — fail fast
        before a massive routing loop. Probe points are in ``crs``."""
        url = f"{self.endpoint.rstrip('/')}/route/v1/{self.profile}/{self._coords([a, b])}"
        params = {"overview": "false", "alternatives": "false", "steps": "false"}
        try:
            payload = self._get_json(url, params)
        except RoutingError as exc:
            raise RoutingError("OSRM_UNAVAILABLE", self.endpoint) from exc
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RoutingError("OSRM_UNAVAILABLE", self.endpoint)

    def route(self, a: Point, b: Point) -> Route:
        url = f"{self.endpoint.rstrip('/')}/route/v1/{self.profile}/{self._coords([a, b])}"
        params = {"overview": "full", "geometries": "geojson", "alternatives": "false"}
        payload = self._get_json(url, params)
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RoutingError(
                "OSRM_NO_ROUTE", f"code={payload.get('code')!r} between {a} and {b}"
            )
        best = payload["routes"][0]
        coords = best.get("geometry", {}).get("coordinates", [])
        geometry = tuple(self._to_crs.transform(lon, lat) for lon, lat in coords)
        raw_distance_m = float(best["distance"])
        distance_m = self._floor_degenerate(a, b, raw_distance_m)
        # Floored degenerate drop: OSRM collapsed the endpoints, its polyline
        # is empty or a single point. Downstream consumers need a non-null
        # trace: fall back to the straight segment, consistent with the
        # tortuosity fallback distance.
        if distance_m != raw_distance_m and len(geometry) < 2:
            geometry = (a, b)
        return Route(distance_m=distance_m, geometry=geometry)

    def distance(self, a: Point, b: Point) -> float:
        return self.route(a, b).distance_m

    # --- BatchRoutingProvider (/table) -------------------------------------- #
    def _table_chunk(
        self, source: Point, destinations: Sequence[Point]
    ) -> list[float | None]:
        """One row of distances (m): ``source`` to each destination.

        Single ``/table`` call with ``sources=0`` and
        ``annotations=distance``; no geometry. ``None`` = unroutable pair
        (OSRM returns ``null`` in the matrix) — the caller decides.
        """
        if not destinations:
            return []
        url = (
            f"{self.endpoint.rstrip('/')}/table/v1/{self.profile}/"
            f"{self._coords([source, *destinations])}"
        )
        params = {
            "sources": "0",
            "destinations": ";".join(str(i + 1) for i in range(len(destinations))),
            "annotations": "distance",
        }
        payload = self._get_json(url, params)
        if payload.get("code") != "Ok":
            raise RoutingError("OSRM_NO_TABLE", f"code={payload.get('code')!r} on /table")
        distances = payload.get("distances")
        if not distances or not isinstance(distances, list) or len(distances) != 1:
            raise RoutingError(
                "OSRM_BAD_PAYLOAD",
                f"unexpected /table matrix: {len(distances or [])} row(s)",
            )
        row = distances[0]
        if len(row) != len(destinations):
            raise RoutingError(
                "OSRM_BAD_PAYLOAD",
                f"/table row of {len(row)} cols for {len(destinations)} destinations",
            )
        # Degenerate cells of the batch are floored too, so they cannot skew a
        # downstream marginal-cost model. The legitimate diagonal
        # (source == destination -> true 0) and None cells are preserved.
        out: list[float | None] = []
        for value, dest in zip(row, destinations, strict=True):
            if value is None:
                out.append(None)
            elif dest == source:
                out.append(float(value))
            else:
                out.append(self._floor_degenerate(source, dest, float(value)))
        return out

    def distance_matrix(
        self,
        sources: Sequence[Point],
        destinations: Sequence[Point],
    ) -> list[list[float | None]]:
        """Bulk distances via OSRM ``/table``.

        Destinations are chunked by ``table_batch_size`` (``None`` = one call
        per source) to respect the instance's ``max-table-size``. No
        geometry. HTTP/payload failures raise :class:`RoutingError` — the
        caller decides the fallback.
        """
        batch = self.table_batch_size
        matrix: list[list[float | None]] = []
        for source in sources:
            row: list[float | None] = []
            if batch is None:
                row.extend(self._table_chunk(source, destinations))
            else:
                for start in range(0, len(destinations), batch):
                    row.extend(
                        self._table_chunk(source, destinations[start : start + batch])
                    )
            matrix.append(row)
        return matrix


# --------------------------------------------------------------------------- #
# Canonical pair keys + pre-routed cache                                       #
# --------------------------------------------------------------------------- #
def endpoint_key(p: Point, *, crs: str, precision_m: float = 1.0) -> str:
    """Canonical key of one endpoint: CRS tag + coordinates rounded to
    ``precision_m``. Providers only ever see coordinates (never feature ids),
    so cache keys are geometric by construction."""
    if precision_m == 1.0:
        return f"{crs}|m1|{round(p[0])},{round(p[1])}"
    return (
        f"{crs}|m{precision_m:g}|"
        f"{round(p[0] / precision_m)},{round(p[1] / precision_m)}"
    )


def route_key(a: Point, b: Point, *, crs: str, precision_m: float = 1.0) -> str:
    """Canonical key of an unordered pair: sha256 of the sorted endpoint keys.

    Direction-independent (undirected pair), so A→B and B→A share one cache
    entry; the cache reverses the stored geometry when serving the swapped
    direction.
    """
    ka = endpoint_key(a, crs=crs, precision_m=precision_m)
    kb = endpoint_key(b, crs=crs, precision_m=precision_m)
    lo, hi = (ka, kb) if ka <= kb else (kb, ka)
    return hashlib.sha256(f"{lo}|{hi}".encode()).hexdigest()


_CACHE_REQUIRED_COLUMNS = frozenset(
    {"route_key", "a_x_m", "a_y_m", "b_x_m", "b_y_m", "distance_m", "geometry"}
)

# Per-route provenance markers (optional ``routing_source`` cache column). A
# ``tortuosity_fallback`` route was BAKED as a tortuosity estimate when the
# cache was built (road router failed on that pair); at run time it is served
# without error, hence invisible unless this column is read.
ROUTING_SOURCE_PROVIDER = "osrm"
ROUTING_SOURCE_TORTUOSITY_FALLBACK = "tortuosity_fallback"


class CachedRoutingProvider:
    """RoutingProvider serving a pre-routed GeoParquet; a miss raises
    ``ROUTE_CACHE_MISS``.

    The cache holds one row per unordered pair: ``route_key`` (canonical,
    see :func:`route_key`), both endpoints, ``distance_m`` and the routed
    geometry, plus two optional columns — ``routing_source`` (provenance:
    which pairs were baked as tortuosity fallbacks) and ``surface_class``.
    Defensive on read: a stored polyline degenerated to a repeated point with
    a positive ``distance_m`` is repaired to the straight segment between the
    stored endpoints (and counted) instead of failing the run.
    """

    def __init__(self, path: Path | str, *, crs: str, precision_m: float = 1.0) -> None:
        from gispulse.persistence.io import read_geoparquet

        cache_path = Path(path)
        if not cache_path.exists():
            raise RoutingError("PREROUTE_CACHE_MISSING", str(cache_path.resolve()))

        gdf = read_geoparquet(str(cache_path))
        missing = _CACHE_REQUIRED_COLUMNS.difference(gdf.columns)
        if missing:
            raise ValueError(f"missing pre-route cache columns: {sorted(missing)}")
        if gdf.crs is None or gdf.crs.to_string().upper() != crs.upper():
            raise ValueError(
                f"pre-route cache CRS mismatch: cache={gdf.crs}, expected {crs}"
            )

        self._crs = crs
        self._precision_m = precision_m
        self._routes: dict[str, Route] = {}
        self._stored_a_keys: dict[str, str] = {}
        self._surface_classes: dict[str, str | None] = {}
        self._has_surface_cache = "surface_class" in gdf.columns
        self._tortuosity_fallback_keys: set[str] = set()
        self._degenerate_geometry_repaired_keys: set[str] = set()
        has_routing_source = "routing_source" in gdf.columns
        for row in gdf.itertuples():
            key = str(row.route_key)
            if key in self._routes:
                raise ValueError(f"duplicate route_key: {key}")

            a = (float(row.a_x_m), float(row.a_y_m))
            b = (float(row.b_x_m), float(row.b_y_m))
            _validate_finite_point(key, "a", a)
            _validate_finite_point(key, "b", b)
            expected = route_key(a, b, crs=crs, precision_m=precision_m)
            if key != expected:
                raise ValueError(
                    f"inconsistent route_key: stored={key} expected={expected} "
                    "(cache built with another CRS/precision?)"
                )
            geometry, repaired = _line_points(row.geometry, endpoints=(a, b))
            distance_m = float(row.distance_m)
            if not math.isfinite(distance_m) or distance_m <= 0:
                raise ValueError(f"invalid distance_m for key {key}: {distance_m!r}")

            self._routes[key] = Route(distance_m=distance_m, geometry=geometry)
            self._stored_a_keys[key] = self._endpoint_key(a)
            if self._has_surface_cache:
                self._surface_classes[key] = _nullable_string(row.surface_class)
            if repaired:
                self._degenerate_geometry_repaired_keys.add(key)
            if (
                has_routing_source
                and str(row.routing_source) == ROUTING_SOURCE_TORTUOSITY_FALLBACK
            ):
                self._tortuosity_fallback_keys.add(key)

    def _endpoint_key(self, p: Point) -> str:
        return endpoint_key(p, crs=self._crs, precision_m=self._precision_m)

    def _route_key(self, a: Point, b: Point) -> str:
        return route_key(a, b, crs=self._crs, precision_m=self._precision_m)

    # --- RoutingProvider --------------------------------------------------- #
    def distance(self, a: Point, b: Point) -> float:
        return self._lookup(a, b).distance_m

    def route(self, a: Point, b: Point) -> Route:
        return self._lookup(a, b)

    # --- provenance / diagnostics ------------------------------------------ #
    def is_tortuosity_fallback(self, a: Point, b: Point) -> bool:
        """Was the served route for this pair BAKED as a tortuosity estimate?

        ``True`` means the cache row carries
        ``routing_source='tortuosity_fallback'`` (the road router failed at
        build time) — not a real routed trace. Direction-independent.
        Coincident endpoints or unknown pair → ``False``.
        """
        if self._endpoint_key(a) == self._endpoint_key(b):
            return False
        return self._route_key(a, b) in self._tortuosity_fallback_keys

    @property
    def has_surface_cache(self) -> bool:
        """Does the cache carry a baked per-route surface column?"""
        return self._has_surface_cache

    def surface_class(self, a: Point, b: Point) -> str | None:
        """Baked surface class for the pair, or None when unknown/uncovered."""
        if self._endpoint_key(a) == self._endpoint_key(b) or not self._has_surface_cache:
            return None
        if self.is_tortuosity_fallback(a, b):
            return None
        return self._surface_classes.get(self._route_key(a, b))

    @property
    def tortuosity_fallback_count(self) -> int:
        """Number of cache routes baked as tortuosity fallbacks."""
        return len(self._tortuosity_fallback_keys)

    @property
    def degenerate_geometry_repaired_count(self) -> int:
        """Number of routes whose degenerate stored polyline was repaired to a
        straight segment on load. > 0 signals a cache worth regenerating."""
        return len(self._degenerate_geometry_repaired_keys)

    # --- internals ---------------------------------------------------------- #
    def _lookup(self, a: Point, b: Point) -> Route:
        if self._endpoint_key(a) == self._endpoint_key(b):
            return Route(distance_m=0.0, geometry=(a, b))
        key = self._route_key(a, b)
        found = self._routes.get(key)
        if found is None:
            raise RoutingError(
                "ROUTE_CACHE_MISS",
                context=f"{self._endpoint_key(a)}->{self._endpoint_key(b)}",
            )
        if self._stored_a_keys[key] != self._endpoint_key(a):
            return Route(
                distance_m=found.distance_m,
                geometry=tuple(reversed(found.geometry)),
            )
        return found


def _line_points(
    geometry: Any, *, endpoints: tuple[Point, Point]
) -> tuple[tuple[Point, ...], bool]:
    """Stored polyline points, or a straight fallback when degenerate.

    Returns ``(points, repaired)``. A valid polyline is returned as is
    (``repaired=False``). A degenerate geometry (wrong type, empty, invalid,
    zero length, < 2 points or non-finite coordinates) is replaced by the
    straight ``endpoints`` segment (``repaired=True``): the endpoints are
    already validated finite, and ``distance_m`` (> 0) stays the routed
    distance. Read-side defense against a corrupted cache, not a run failure.
    """
    from shapely.geometry import LineString

    if not isinstance(geometry, LineString) or geometry.is_empty:
        return endpoints, True
    if not geometry.is_valid or geometry.length == 0.0:
        return endpoints, True
    points = tuple((float(c[0]), float(c[1])) for c in geometry.coords)
    if len(points) < 2:
        return endpoints, True
    for point in points:
        if not (math.isfinite(point[0]) and math.isfinite(point[1])):
            return endpoints, True
    return points, False


def _nullable_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value)
    if text in ("", "nan", "<NA>", "None"):
        return None
    return text


def _validate_finite_point(key: str, label: str, point: Point) -> None:
    if not math.isfinite(point[0]) or not math.isfinite(point[1]):
        raise ValueError(f"non-finite coordinates for key {key}: {label}={point!r}")


__all__ = [
    "Point",
    "Route",
    "RoutingError",
    "RoutingProvider",
    "BatchRoutingProvider",
    "TortuosityBand",
    "TortuosityProvider",
    "OSRMProvider",
    "OSRMDropTelemetry",
    "OSRMDropTelemetrySnapshot",
    "CachedRoutingProvider",
    "ROUTING_SOURCE_PROVIDER",
    "ROUTING_SOURCE_TORTUOSITY_FALLBACK",
    "endpoint_key",
    "route_key",
    "euclidean_length",
    "straight_distance",
    "tortuosity_factor",
    "tortuous_distance",
]
