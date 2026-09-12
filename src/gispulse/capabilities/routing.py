"""Road-routing capability for GISPulse.

Requires optional dependencies:
    - httpx     (``provider="osrm"`` only; core dependency in practice)
    - shapely   (already a geopandas dependency)

Bridges the :mod:`gispulse.core.routing_providers` abstraction into the
pipeline: routes point pairs through a road network (OSRM), a calibrated
tortuosity estimate (offline), or a pre-routed GeoParquet cache.

Unlike the :mod:`gispulse.capabilities.network` family, which routes *inside
a line layer you provide*, ``route_pairs`` asks an external road model for
the path between two points — no network layer needed.

Requires ``tier="pro"`` like the rest of the routing/network family.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, Point as ShapelyPoint

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.core.routing_providers import (
    ROUTING_SOURCE_TORTUOSITY_FALLBACK,
    CachedRoutingProvider,
    OSRMProvider,
    RoutingError,
    RoutingProvider,
    TortuosityBand,
    TortuosityProvider,
    straight_distance,
)
from gispulse.persistence.tier import check_tier

# Column contract of the emitted routed pairs. Module constant so the
# empty-result branches and the populated branch stay in sync.
_ROUTE_PAIRS_COLUMNS = [
    "pair_id",
    "distance_m",
    "straight_m",
    "routing_source",
    "geometry",
]

# RoutingError codes that mean "this pair has no route" (droppable with
# on_no_route="skip"); every other code is an infrastructure failure and
# always raises.
_NO_ROUTE_CODES = frozenset({"OSRM_NO_ROUTE", "ROUTE_CACHE_MISS"})


def _parse_bands(raw: Any) -> tuple[TortuosityBand, ...]:
    """Validate and convert the ``tortuosity_bands`` parameter.

    Expected: a list of ``{"max_straight_m": number | null, "factor": number}``
    evaluated in order (make the last band ``max_straight_m=null`` to catch
    all larger distances). Default: a single identity band (factor 1.0 —
    straight-line distance; a real detour factor is an explicit choice, not a
    hidden default).
    """
    if raw is None:
        return (TortuosityBand(max_straight_m=None, factor=1.0),)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(
            "route_pairs: tortuosity_bands must be a non-empty list of "
            "{max_straight_m, factor} objects."
        )
    bands: list[TortuosityBand] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "factor" not in item:
            raise ValueError(
                f"route_pairs: tortuosity_bands[{i}] must be an object with a "
                "'factor' key (and optional 'max_straight_m')."
            )
        factor = float(item["factor"])
        if factor <= 0:
            raise ValueError(
                f"route_pairs: tortuosity_bands[{i}].factor must be > 0."
            )
        max_raw = item.get("max_straight_m")
        bands.append(
            TortuosityBand(
                max_straight_m=None if max_raw is None else float(max_raw),
                factor=factor,
            )
        )
    return tuple(bands)


@register
class RoutePairsCapability(Capability):
    """Route des paires de points par la voirie (OSRM), tortuosité ou cache."""

    name = "route_pairs"
    description = (
        "Routes point pairs (row i of the input layer to row i of the "
        "ref_layer) and returns one line per pair with its routed distance. "
        "provider='tortuosity' (default, offline): distance = straight-line "
        "× calibrated detour factor per distance band, straight geometry. "
        "provider='osrm': real road routing against an OSRM endpoint, with "
        "degenerate-drop flooring back to the tortuosity bands. "
        "provider='cached': serves a pre-routed GeoParquet keyed by "
        "canonical pair keys. Unlike the network_* capabilities this needs "
        "no line layer — the road model is external."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        provider: str = "tortuosity",
        tortuosity_bands: Any = None,
        osrm_endpoint: str | None = None,
        osrm_profile: str = "driving",
        timeout_s: float = 10.0,
        degenerate_epsilon_m: float | None = None,
        cache_path: str | None = None,
        key_precision_m: float = 1.0,
        on_no_route: str = "raise",
        crs_meters: str | None = None,
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Points origine (une ligne par paire ; les
                            géométries non ponctuelles utilisent leur
                            centroïde).
            ref_gdf:        Points destination (via ``ref_layer``), appariés
                            **par position** : ligne i ↔ ligne i. Longueurs
                            différentes = erreur (fail-fast).
            provider:       ``"tortuosity"`` (défaut, hors-ligne),
                            ``"osrm"`` ou ``"cached"``.
            tortuosity_bands: Paliers ``[{max_straight_m, factor}, ...]``
                            évalués dans l'ordre (dernier palier
                            ``max_straight_m=null`` = attrape-tout). Défaut :
                            facteur 1.0 (vol d'oiseau) — un facteur de détour
                            réel est un choix explicite. Sert aussi de
                            plancher aux drops dégénérés OSRM.
            osrm_endpoint:  URL de l'instance OSRM (requis si
                            ``provider="osrm"``).
            osrm_profile:   Profil OSRM (défaut ``driving``).
            timeout_s:      Timeout HTTP par requête OSRM.
            degenerate_epsilon_m: Seuil « effectivement nul » du planchage des
                            drops dégénérés OSRM (``null`` = strict <= 0).
            cache_path:     GeoParquet pré-routé (requis si
                            ``provider="cached"``). Son CRS doit être le CRS
                            de travail.
            key_precision_m: Arrondi (m) des clés canoniques du cache.
            on_no_route:    ``"raise"`` (défaut) ou ``"skip"`` — ne concerne
                            que les paires sans itinéraire (OSRM_NO_ROUTE /
                            ROUTE_CACHE_MISS) ; les pannes d'infrastructure
                            (OSRM injoignable…) lèvent toujours.
            crs_meters:     CRS métrique de travail quand les couches sont
                            angulaires (défaut EPSG:3857).

        Returns:
            GeoDataFrame, une ligne par paire routée : ``pair_id`` (position
            de la ligne d'entrée), ``distance_m`` (distance routée),
            ``straight_m`` (vol d'oiseau, pour lire le détour), ``routing_source``
            (``tortuosity`` / ``osrm`` / ``cached`` /
            ``tortuosity_fallback`` pour une route de cache cuite en repli),
            ``geometry`` (trace routée, ou segment droit sans trace réelle).
            Reprojeté vers le CRS d'origine du ``gdf``.

        Raises:
            ValueError: couche destination absente, longueurs différentes,
                provider inconnu, paramètre provider manquant, ou paliers
                invalides.
            RoutingError: échec de routage (code machine-readable) — sauf
                paires sans itinéraire quand ``on_no_route="skip"``.
        """
        check_tier("pro")

        if provider not in ("tortuosity", "osrm", "cached"):
            raise ValueError(
                f"route_pairs: unknown provider {provider!r} "
                "(expected 'tortuosity', 'osrm' or 'cached')."
            )
        if on_no_route not in ("raise", "skip"):
            raise ValueError(
                f"route_pairs: on_no_route must be 'raise' or 'skip' (got {on_no_route!r})."
            )
        if ref_gdf is None:
            raise ValueError(
                "route_pairs requires a destination layer (inject via ref_layer)."
            )
        if len(gdf) != len(ref_gdf):
            raise ValueError(
                f"route_pairs: origin and destination layers must have the "
                f"same length (got {len(gdf)} vs {len(ref_gdf)})."
            )

        original_crs = gdf.crs
        if gdf.empty:
            return gpd.GeoDataFrame(columns=_ROUTE_PAIRS_COLUMNS, crs=original_crs)

        bands = _parse_bands(tortuosity_bands)

        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        origins_m = gdf.to_crs(effective_crs) if reproject else gdf
        working_crs = origins_m.crs
        dest_m = (
            ref_gdf.to_crs(working_crs)
            if ref_gdf.crs is not None and ref_gdf.crs != working_crs
            else ref_gdf
        )

        routing: RoutingProvider
        if provider == "tortuosity":
            routing = TortuosityProvider(bands=bands)
        elif provider == "osrm":
            if not osrm_endpoint:
                raise ValueError(
                    "route_pairs: provider='osrm' requires osrm_endpoint."
                )
            routing = OSRMProvider(
                endpoint=osrm_endpoint,
                crs=str(working_crs),
                profile=osrm_profile,
                timeout_s=timeout_s,
                floor_bands=bands,
                degenerate_epsilon_m=degenerate_epsilon_m,
            )
        else:
            if not cache_path:
                raise ValueError(
                    "route_pairs: provider='cached' requires cache_path."
                )
            routing = CachedRoutingProvider(
                cache_path, crs=str(working_crs), precision_m=key_precision_m
            )

        rows: list[dict[str, Any]] = []
        for pair_id, (o_geom, d_geom) in enumerate(
            zip(origins_m.geometry, dest_m.geometry)
        ):
            if o_geom is None or o_geom.is_empty or d_geom is None or d_geom.is_empty:
                continue
            o_pt = o_geom if o_geom.geom_type == "Point" else o_geom.centroid
            d_pt = d_geom if d_geom.geom_type == "Point" else d_geom.centroid
            a = (float(o_pt.x), float(o_pt.y))
            b = (float(d_pt.x), float(d_pt.y))
            try:
                route = routing.route(a, b)
            except RoutingError as exc:
                if on_no_route == "skip" and exc.code in _NO_ROUTE_CODES:
                    continue
                raise
            geometry = (
                LineString(route.geometry)
                if len(route.geometry) >= 2
                else LineString([ShapelyPoint(a), ShapelyPoint(b)])
            )
            source = provider
            if provider == "cached" and isinstance(routing, CachedRoutingProvider):
                if routing.is_tortuosity_fallback(a, b):
                    source = ROUTING_SOURCE_TORTUOSITY_FALLBACK
            rows.append(
                {
                    "pair_id": pair_id,
                    "distance_m": float(route.distance_m),
                    "straight_m": straight_distance(a, b),
                    "routing_source": source,
                    "geometry": geometry,
                }
            )

        if not rows:
            return gpd.GeoDataFrame(columns=_ROUTE_PAIRS_COLUMNS, crs=original_crs)

        result = gpd.GeoDataFrame(rows, geometry="geometry", crs=working_crs)
        if reproject and original_crs is not None:
            result = result.to_crs(original_crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Destination points layer, paired row-by-row with the input.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["tortuosity", "osrm", "cached"],
                    "default": "tortuosity",
                    "description": "Routing backend: offline detour estimate, OSRM instance, or pre-routed GeoParquet.",
                },
                "tortuosity_bands": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "properties": {
                            "max_straight_m": {"type": ["number", "null"]},
                            "factor": {"type": "number"},
                        },
                        "required": ["factor"],
                    },
                    "description": (
                        "Ordered detour-factor bands; last band max_straight_m=null "
                        "catches all. Default: single factor 1.0 (straight line)."
                    ),
                },
                "osrm_endpoint": {
                    "type": ["string", "null"],
                    "description": "OSRM base URL (required for provider='osrm').",
                },
                "osrm_profile": {"type": "string", "default": "driving"},
                "timeout_s": {"type": "number", "default": 10.0},
                "degenerate_epsilon_m": {
                    "type": ["number", "null"],
                    "default": None,
                    "description": "'Effectively zero' threshold for OSRM degenerate-drop flooring.",
                },
                "cache_path": {
                    "type": ["string", "null"],
                    "description": "Pre-routed GeoParquet (required for provider='cached').",
                },
                "key_precision_m": {"type": "number", "default": 1.0},
                "on_no_route": {
                    "type": "string",
                    "enum": ["raise", "skip"],
                    "default": "raise",
                    "description": "Pairs without a route: fail the run, or drop the pair.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric working CRS for angular layers. Use EPSG:2154 in France.",
                },
            },
        }


# --------------------------------------------------------------------------- #
# Detour-band calibration                                                      #
# --------------------------------------------------------------------------- #

# Column contract of the calibration output.
_CALIBRATE_COLUMNS = [
    "band_index",
    "max_straight_m",
    "current_factor",
    "recommended_factor",
    "applied_factor",
    "sample_count",
    "used_observations",
    "skipped_observations",
    "geometry",
]


@register
class CalibrateDetourBandsCapability(Capability):
    """Recale les facteurs de détour (tortuosité) contre des distances routées réelles."""

    name = "calibrate_detour_bands"
    description = (
        "Recalibrates the detour ('tortuosity') factors of route_pairs "
        "against real routed distances. Feed it routed observations (e.g. "
        "the output of route_pairs with provider='osrm': straight_m + "
        "distance_m columns); for each band the recommended factor is the "
        "median routed/straight ratio of the observations falling in the "
        "band. Band bounds are never recomputed — only the factors. A band "
        "with no observation keeps its current factor (sample_count=0 tells "
        "you it was not recalibrated)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        bands: Any = None,
        straight_col: str = "straight_m",
        routed_col: str = "distance_m",
        min_straight_m: float = 0.0,
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Observations routées — typiquement la sortie de
                            ``route_pairs`` (provider OSRM) : une ligne par
                            paire mesurée.
            bands:          Paliers courants ``[{max_straight_m, factor}, ...]``
                            (même format que ``tortuosity_bands`` de
                            ``route_pairs``). Les bornes sont conservées, seuls
                            les facteurs sont recalés. Défaut : un palier
                            unique attrape-tout de facteur 1.0 (calibre un
                            facteur global).
            straight_col:   Colonne de distance à vol d'oiseau (m).
            routed_col:     Colonne de distance routée réelle (m).
            min_straight_m: Seuil bas (m) sous lequel une observation est
                            rejetée (bruit du routeur sur les très courtes
                            paires).

        Returns:
            GeoDataFrame, une ligne par palier : ``band_index``,
            ``max_straight_m``, ``current_factor``, ``recommended_factor``
            (médiane des ratios ; NaN quand aucune observation),
            ``applied_factor`` (recommandé, ou courant faute d'observation —
            la liste prête à réinjecter dans ``tortuosity_bands``),
            ``sample_count``, ``used_observations`` / ``skipped_observations``
            (répétés, totaux du run), ``geometry`` (union des géométries des
            observations du palier, None quand vide).

        Raises:
            ValueError: colonne absente, ou paliers invalides.

        Note (fidélité de port): la source historique affectait une
        observation à son palier par borne haute *exclusive*, alors que son
        provider sélectionnait le facteur par borne *inclusive*. Le port
        aligne les deux sur la règle inclusive du provider (une observation à
        exactement ``max_straight_m`` calibre le palier qu'elle utiliserait).
        """
        check_tier("pro")

        parsed_bands = _parse_bands(bands)
        for col in (straight_col, routed_col):
            if col not in gdf.columns:
                raise ValueError(
                    f"calibrate_detour_bands: column {col!r} not in the "
                    "observation layer."
                )

        ratios_by_band: list[list[float]] = [[] for _ in parsed_bands]
        geoms_by_band: list[list[Any]] = [[] for _ in parsed_bands]
        used = 0
        skipped = 0
        for row in gdf.itertuples():
            straight = float(getattr(row, straight_col))
            routed = float(getattr(row, routed_col))
            if (
                not math.isfinite(straight)
                or not math.isfinite(routed)
                or straight <= 0
                or straight < min_straight_m
            ):
                skipped += 1
                continue
            idx = next(
                i
                for i, band in enumerate(parsed_bands)
                if band.max_straight_m is None or straight <= band.max_straight_m
            )
            ratios_by_band[idx].append(routed / straight)
            geom = getattr(row, "geometry", None)
            if geom is not None and not geom.is_empty:
                geoms_by_band[idx].append(geom)
            used += 1

        from shapely.ops import unary_union

        rows: list[dict[str, Any]] = []
        for i, (band, ratios, geoms) in enumerate(
            zip(parsed_bands, ratios_by_band, geoms_by_band, strict=True)
        ):
            recommended = statistics.median(ratios) if ratios else None
            rows.append(
                {
                    "band_index": i,
                    "max_straight_m": band.max_straight_m,
                    "current_factor": band.factor,
                    "recommended_factor": recommended,
                    # Without observations the current factor is kept: the
                    # applied_factor column is directly reusable as
                    # tortuosity_bands input.
                    "applied_factor": recommended if recommended is not None else band.factor,
                    "sample_count": len(ratios),
                    "used_observations": used,
                    "skipped_observations": skipped,
                    "geometry": unary_union(geoms) if geoms else None,
                }
            )

        return gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "bands": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "properties": {
                            "max_straight_m": {"type": ["number", "null"]},
                            "factor": {"type": "number"},
                        },
                        "required": ["factor"],
                    },
                    "description": (
                        "Current detour bands; bounds are kept, only factors are "
                        "recalibrated. Default: one catch-all band, factor 1.0."
                    ),
                },
                "straight_col": {"type": "string", "default": "straight_m"},
                "routed_col": {"type": "string", "default": "distance_m"},
                "min_straight_m": {
                    "type": "number",
                    "default": 0.0,
                    "description": "Observations below this straight-line distance are skipped.",
                },
            },
        }
