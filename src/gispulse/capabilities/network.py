"""
Network capabilities for GISPulse.

Requires optional dependencies:
    - networkx  (ShortestPathCapability, IsochroneCapability,
                 NetworkAllocationCapability, ConnectivityCheckCapability)
    - shapely   (already a geopandas dependency)

These capabilities work in offline/session mode (no PostGIS required).
For production routing on large networks, prefer pgRouting via PostGISSQLCapability.

All capabilities in this module require tier="pro".
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.persistence.tier import check_tier


def _build_graph(
    gdf: gpd.GeoDataFrame,
    weight_col: str | None = None,
) -> tuple[Any, dict[Any, int]]:
    """Construit un graphe NetworkX depuis un GeoDataFrame de lignes.

    Chaque extrémité de ligne (arrondie à 6 décimales) devient un nœud.
    Le poids d'un arc est `weight_col` si fourni, sinon la longueur géographique.

    Returns:
        (graph, node_index) — le graphe et un dict {(x,y) -> node_id}.
    """
    try:
        import networkx as nx
    except ImportError as exc:
        raise ImportError(
            "Network capabilities require 'networkx'. "
            "Install with: pip install networkx"
        ) from exc

    G = nx.Graph()
    node_idx: dict[tuple[float, float], int] = {}

    def _snap(pt: Point) -> tuple[float, float]:
        return (round(pt.x, 6), round(pt.y, 6))

    def _node(pt: Point) -> int:
        key = _snap(pt)
        if key not in node_idx:
            nid = len(node_idx)
            node_idx[key] = nid
            G.add_node(nid, x=key[0], y=key[1])
        return node_idx[key]

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        lines = geom.geoms if isinstance(geom, MultiLineString) else [geom]
        for line in lines:
            if not isinstance(line, LineString) or len(line.coords) < 2:
                continue
            coords = list(line.coords)
            start = _node(Point(coords[0]))
            end = _node(Point(coords[-1]))
            weight = (
                float(row[weight_col])
                if weight_col and weight_col in gdf.columns
                else line.length
            )
            G.add_edge(start, end, weight=weight, geometry=line)

    return G, node_idx


def _nearest_node(
    pt: Point,
    node_idx: dict[tuple[float, float], int],
) -> int:
    """Retourne l'identifiant du nœud le plus proche d'un point."""
    best_id = -1
    best_dist = float("inf")
    for (x, y), nid in node_idx.items():
        d = (x - pt.x) ** 2 + (y - pt.y) ** 2
        if d < best_dist:
            best_dist = d
            best_id = nid
    return best_id


@register
class ShortestPathCapability(Capability):
    """Calcule le plus court chemin dans un réseau de lignes (NetworkX Dijkstra)."""

    name = "shortest_path"
    description = "Finds shortest path between two points through a line network (NetworkX)."

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        start_x: float = 0.0,
        start_y: float = 0.0,
        end_x: float = 0.0,
        end_y: float = 0.0,
        weight_col: str | None = None,
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Réseau de lignes (GeoDataFrame de LineString/MultiLineString).
            start_x:    Longitude/X du point de départ (CRS du ``gdf``).
            start_y:    Latitude/Y du point de départ (CRS du ``gdf``).
            end_x:      Longitude/X du point d'arrivée (CRS du ``gdf``).
            end_y:      Latitude/Y du point d'arrivée (CRS du ``gdf``).
            weight_col: Colonne utilisée comme poids des arcs. Si None,
                        longueur géométrique calculée après reprojection.
            crs_meters: CRS métrique pour le routing. Si le réseau est en
                        CRS angulaire (lat/lon), il est reprojeté en
                        ``crs_meters`` avant la construction du graphe
                        pour que les longueurs d'arcs soient en mètres
                        réels. Default EPSG:3857 quand la reprojection
                        est nécessaire.

        Returns:
            GeoDataFrame des arcs constituant le chemin, avec colonne
            ``path_order``, reprojeté vers la CRS d'origine du ``gdf``.
        """
        check_tier("pro")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("ShortestPathCapability requires 'networkx'.") from exc

        original_crs = gdf.crs
        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        if reproject:
            network_m = gdf.to_crs(effective_crs)
            src_pt = gpd.GeoSeries([Point(start_x, start_y)], crs=original_crs).to_crs(effective_crs).iloc[0]
            dst_pt = gpd.GeoSeries([Point(end_x, end_y)], crs=original_crs).to_crs(effective_crs).iloc[0]
        else:
            network_m = gdf
            src_pt = Point(start_x, start_y)
            dst_pt = Point(end_x, end_y)

        G, node_idx = _build_graph(network_m, weight_col)
        start_node = _nearest_node(src_pt, node_idx)
        end_node = _nearest_node(dst_pt, node_idx)

        try:
            path_nodes = nx.shortest_path(G, start_node, end_node, weight="weight")
        except nx.NetworkXNoPath:
            return gpd.GeoDataFrame(columns=["path_order", "geometry"], crs=original_crs)

        path_edges = []
        for i, (u, v) in enumerate(zip(path_nodes[:-1], path_nodes[1:])):
            edge_data = G.edges[u, v]
            path_edges.append({"path_order": i, "geometry": edge_data["geometry"]})

        if not path_edges:
            return gpd.GeoDataFrame(columns=["path_order", "geometry"], crs=original_crs)

        result = gpd.GeoDataFrame(path_edges, geometry="geometry", crs=network_m.crs)
        if reproject and original_crs is not None:
            result = result.to_crs(original_crs)
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_x": {"type": "number"},
                "start_y": {"type": "number"},
                "end_x": {"type": "number"},
                "end_y": {"type": "number"},
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column for arc weight. If null, geographic length after metric reprojection.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS used to reproject an angular network. Use EPSG:2154 in France for accurate meter-scale routing.",
                },
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        }


@register
class IsochroneCapability(Capability):
    """Calcule des zones isochrones (nœuds atteignables dans un budget de coût) sur un réseau.

    Deux modes d'usage :

    1. **Point unique** — ``gdf`` est le réseau de lignes, ``start_x``/``start_y``
       sont les coordonnées du point de départ. Compat historique.

    2. **Batch multi-sources** — ``gdf`` est un layer de features sources
       (points ou polygones), ``ref_layer`` référence le réseau routier.
       L'isochrone est calculée depuis chaque centroïde en une seule passe
       Dijkstra multi-sources puis les arcs atteints sont buffer+dissous en
       polygone de couverture.

    Le paramètre ``crs_meters`` reprojette le réseau en CRS métrique pour que
    ``cost_budget`` soit exprimé en mètres (indispensable quand les données
    sont en EPSG:4326).
    """

    name = "isochrone"
    description = (
        "Computes network-based isochrones. Supports single-point mode "
        "(gdf = network) or batch mode (gdf = facilities, ref_layer = network) "
        "with multi-source Dijkstra and metric CRS support."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        start_x: float = 0.0,
        start_y: float = 0.0,
        cost_budget: float = 1000.0,
        cost_budgets: list[float] | None = None,
        weight_col: str | None = None,
        dissolve: bool = True,
        ref_gdf: gpd.GeoDataFrame | None = None,
        crs_meters: str = "EPSG:3857",
        edge_buffer_m: float = 30.0,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Réseau de lignes (mode point unique) OU layer de
                           sources (mode batch si ref_gdf fourni).
            start_x:       X du point de départ (mode point unique).
            start_y:       Y du point de départ (mode point unique).
            cost_budget:   Budget de coût maximal, exprimé dans l'unité de
                           weight_col, ou en mètres si weight_col=None et
                           crs_meters est un CRS projeté.
            cost_budgets:  Liste de budgets pour émettre N anneaux concentriques
                           en un seul passage Dijkstra (cutoff=max). Chaque
                           anneau est une zone pleine (pas un anneau évidé) —
                           classify_by_ring aval choisit l'anneau le plus
                           interne qui contient un feature. Si fourni, prend
                           le pas sur ``cost_budget``. Requiert ``dissolve=True``.
            weight_col:    Colonne de poids des arcs. Si None, longueur
                           géographique (en mètres si crs_meters projeté).
            dissolve:      Si True, retourne un polygone de couverture
                           (buffer+union des arcs atteints). Si False,
                           retourne les arcs bruts.
            ref_gdf:       Réseau de lignes passé via ``ref_layer`` (mode
                           batch). Active l'isochrone multi-sources depuis
                           chaque centroïde de ``gdf``.
            crs_meters:    CRS métrique utilisé pour le routing. Default
                           EPSG:3857 pour compat ; préférer EPSG:2154 en
                           France pour des distances exactes.
            edge_buffer_m: Largeur en mètres du buffer appliqué aux arcs
                           atteints pour former le polygone isochrone.

        Returns:
            GeoDataFrame du polygone (dissolve=True) ou des arcs atteignables.
            Avec ``cost_budgets``, une feature par budget avec la colonne
            ``cost_budget``. Toujours reprojeté vers la CRS d'origine.
        """
        check_tier("pro")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("IsochroneCapability requires 'networkx'.") from exc

        multi_budget = cost_budgets is not None and len(cost_budgets) > 0
        if multi_budget and not dissolve:
            raise ValueError(
                "isochrone: cost_budgets (list) requires dissolve=True — raw-edge "
                "mode emits only a single reachable set."
            )
        budgets: list[float] = (
            sorted({float(b) for b in cost_budgets}) if multi_budget else [float(cost_budget)]
        )
        if any(b < 0 for b in budgets):
            raise ValueError("isochrone: cost_budget(s) must be >= 0.")
        # P2 (beta-test 2026-04-24): a budget of 0 used to produce a
        # degenerate ~30 m buffer ring around the start node because the
        # start itself has ``d == 0`` so ``reachable`` was non-empty and
        # the edge-buffer step still ran. Semantically "reach with zero
        # budget" means nothing is reachable beyond the seed point, so
        # the only honest answer is an empty isochrone — short-circuit
        # before invoking Dijkstra. Result CRS mirrors the regular
        # path: ``gdf.crs`` in batch mode, ``ref_gdf or gdf`` in point
        # mode (resolved below as ``result_crs``).
        if all(b == 0 for b in budgets):
            return gpd.GeoDataFrame(
                columns=["geometry", "cost_budget"], crs=gdf.crs
            )

        batch_mode = ref_gdf is not None and not ref_gdf.empty

        network = ref_gdf if batch_mode else gdf
        source_crs = network.crs
        result_crs = gdf.crs if batch_mode else network.crs

        # Reproject network to metric CRS so cost_budget is in meters when
        # the source data is in lat/lon. Applied only in batch mode — the
        # legacy single-point mode keeps cost_budget in the native CRS
        # units for backward compatibility. Users who want metric routing
        # in point mode should reproject the network themselves before
        # calling, or use the batch mode with a single-feature gdf.
        if batch_mode and is_angular(network):
            network_m = network.to_crs(crs_meters)
        else:
            network_m = network

        G, node_idx = _build_graph(network_m, weight_col)
        if len(node_idx) == 0:
            return gpd.GeoDataFrame(columns=["geometry", "cost_budget"], crs=result_crs)

        # Collect start nodes — one per source feature (batch) or a single
        # node from start_x/start_y (point mode).
        if batch_mode:
            sources_m = gdf.to_crs(crs_meters) if is_angular(gdf) else gdf
            start_nodes: set[int] = set()
            for geom in sources_m.geometry:
                if geom is None or geom.is_empty:
                    continue
                pt = geom.centroid if geom.geom_type != "Point" else geom
                start_nodes.add(_nearest_node(pt, node_idx))
            if not start_nodes:
                return gpd.GeoDataFrame(columns=["geometry", "cost_budget"], crs=result_crs)
        else:
            start_nodes = {_nearest_node(Point(start_x, start_y), node_idx)}

        # Single Dijkstra pass for all budgets — cutoff at the largest, then
        # filter reachable nodes per budget. N-budget cost ≈ 1-budget cost.
        max_cutoff = budgets[-1]
        lengths = nx.multi_source_dijkstra_path_length(
            G, sources=start_nodes, cutoff=max_cutoff, weight="weight"
        )
        if not lengths:
            return gpd.GeoDataFrame(columns=["geometry", "cost_budget"], crs=result_crs)

        if multi_budget:
            from shapely.ops import unary_union

            buffer_m = max(edge_buffer_m, 1.0)
            all_edges = list(G.edges(data=True))
            rings: list[dict] = []
            for b in budgets:
                reachable_b = {n for n, d in lengths.items() if d <= b}
                if not reachable_b:
                    continue
                geoms = [
                    data["geometry"]
                    for u, v, data in all_edges
                    if u in reachable_b or v in reachable_b
                ]
                if not geoms:
                    continue
                zone = unary_union([g.buffer(buffer_m) for g in geoms])
                rings.append({"geometry": zone, "cost_budget": b})
            if not rings:
                return gpd.GeoDataFrame(columns=["geometry", "cost_budget"], crs=result_crs)
            out = gpd.GeoDataFrame(rings, crs=network_m.crs)
            return out.to_crs(result_crs) if result_crs is not None else out

        reachable = {n for n, d in lengths.items() if d <= budgets[0]}

        edges = []
        for u, v, data in G.edges(data=True):
            if u in reachable or v in reachable:
                edges.append({"geometry": data["geometry"], "cost": data["weight"]})

        if not edges:
            return gpd.GeoDataFrame(columns=["geometry", "cost_budget"], crs=result_crs)

        result_m = gpd.GeoDataFrame(edges, crs=network_m.crs)

        if dissolve:
            from shapely.ops import unary_union
            buffered = result_m.geometry.buffer(max(edge_buffer_m, 1.0))
            zone = unary_union(list(buffered))
            out = gpd.GeoDataFrame(
                [{"geometry": zone, "cost_budget": budgets[0]}],
                crs=network_m.crs,
            )
            return out.to_crs(result_crs) if result_crs is not None else out

        return result_m.to_crs(result_crs).reset_index(drop=True) if result_crs is not None else result_m.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_x": {"type": "number", "default": 0.0},
                "start_y": {"type": "number", "default": 0.0},
                "cost_budget": {"type": "number", "default": 1000.0},
                "cost_budgets": {
                    "type": ["array", "null"],
                    "items": {"type": "number"},
                    "description": (
                        "List of budgets for N concentric rings in one "
                        "Dijkstra pass; overrides cost_budget when set."
                    ),
                },
                "weight_col": {"type": ["string", "null"]},
                "dissolve": {"type": "boolean", "default": True},
                "ref_layer": {
                    "type": ["string", "null"],
                    "description": "Batch mode — name of the road-network layer. When set, isochrones are computed from each feature centroid in the input gdf.",
                },
                "crs_meters": {
                    "type": "string",
                    "default": "EPSG:3857",
                    "description": "Metric CRS for routing. Use EPSG:2154 in France for accurate distances.",
                },
                "edge_buffer_m": {
                    "type": "number",
                    "default": 30.0,
                    "description": "Buffer (meters) applied to reached edges when dissolve=True.",
                },
            },
        }


@register
class NetworkAllocationCapability(Capability):
    """Alloue chaque feature d'un layer au nœud réseau le plus proche.

    Cas d'usage FTTH : rattacher chaque abonné (point) au NRO/NRA le plus proche
    dans le réseau de fibre, en remontant les arcs de moindre coût.
    """

    name = "network_allocation"
    description = (
        "Allocates each feature in a point layer to its nearest network node, "
        "optionally constrained by a max cost (e.g. connect subscribers to NROs)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        network_gdf: gpd.GeoDataFrame | None = None,
        hubs_gdf: gpd.GeoDataFrame | None = None,
        hub_id_col: str = "id",
        max_cost: float | None = None,
        weight_col: str | None = None,
        crs_meters: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:          Couche de points à rattacher (abonnés, bâtiments…).
            network_gdf:  Réseau de lignes (arcs). Obligatoire.
            hubs_gdf:     Couche de points représentant les hubs/NROs. Obligatoire.
            hub_id_col:   Colonne identifiant le hub dans hubs_gdf.
            max_cost:     Coût maximum de rattachement, en mètres après
                          reprojection métrique (ou dans l'unité de
                          ``weight_col`` si fourni). Si None, pas de limite.
            weight_col:   Colonne de poids des arcs. Si None, longueur
                          géométrique calculée après reprojection métrique.
            crs_meters:   CRS métrique utilisé pour le routing. Si les
                          couches sont en CRS angulaire (lat/lon), elles
                          sont reprojetées en ``crs_meters`` avant le
                          calcul pour que les coûts soient en mètres
                          réels. Default EPSG:3857 quand nécessaire.

        Returns:
            GeoDataFrame des features d'entrée enrichi des colonnes :
            - ``allocated_hub_id`` : identifiant du hub rattaché.
            - ``allocation_cost``  : coût du chemin jusqu'au hub, en mètres
              (ou unité de ``weight_col``).
            - ``allocated``        : booléen — True si un hub a été trouvé dans le budget.
        """
        check_tier("pro")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("NetworkAllocationCapability requires 'networkx'.") from exc

        if network_gdf is None:
            raise ValueError("NetworkAllocationCapability requires 'network_gdf'.")
        if hubs_gdf is None:
            raise ValueError("NetworkAllocationCapability requires 'hubs_gdf'.")
        if gdf.empty:
            result = gdf.copy()
            result["allocated_hub_id"] = None
            result["allocation_cost"] = float("nan")
            result["allocated"] = False
            return result

        # Reproject every layer to the same metric CRS when any of them
        # is angular, so edge lengths and hub coordinates are all in
        # the same metric system.
        effective_crs = crs_meters or "EPSG:3857"
        reproject = is_angular(network_gdf) or is_angular(hubs_gdf) or is_angular(gdf)
        if reproject:
            net_m = network_gdf.to_crs(effective_crs) if is_angular(network_gdf) else network_gdf
            hubs_m = hubs_gdf.to_crs(effective_crs) if is_angular(hubs_gdf) else hubs_gdf
            feats_m = gdf.to_crs(effective_crs) if is_angular(gdf) else gdf
        else:
            net_m = network_gdf
            hubs_m = hubs_gdf
            feats_m = gdf

        G, node_idx = _build_graph(net_m, weight_col)

        # Snap chaque hub sur le graphe
        hub_nodes: list[tuple[int, Any]] = []
        for _, hub_row in hubs_m.iterrows():
            if hub_row.geometry is None or hub_row.geometry.is_empty:
                continue
            hub_pt = hub_row.geometry
            nid = _nearest_node(Point(hub_pt.x, hub_pt.y), node_idx)
            hub_id_val = hub_row.get(hub_id_col, hub_row.name)
            hub_nodes.append((nid, hub_id_val))

        if not hub_nodes:
            result = gdf.copy()
            result["allocated_hub_id"] = None
            result["allocation_cost"] = float("nan")
            result["allocated"] = False
            return result

        # Dijkstra multi-source depuis tous les hubs
        node_to_hub: dict[int, tuple[Any, float]] = {}  # node_id -> (hub_id, cost)

        for hub_nid, hub_val in hub_nodes:
            if hub_nid not in G:
                continue
            lengths = nx.single_source_dijkstra_path_length(
                G, hub_nid, cutoff=max_cost, weight="weight"
            )
            for reached_nid, cost in lengths.items():
                if reached_nid not in node_to_hub or cost < node_to_hub[reached_nid][1]:
                    node_to_hub[reached_nid] = (hub_val, cost)

        # Rattachement de chaque feature
        hub_ids = []
        costs = []
        allocated_flags = []

        for _, feat_row in feats_m.iterrows():
            if feat_row.geometry is None or feat_row.geometry.is_empty:
                hub_ids.append(None)
                costs.append(float("nan"))
                allocated_flags.append(False)
                continue

            feat_pt = feat_row.geometry
            # Si c'est un polygone ou une ligne, on prend le centroïde
            if not isinstance(feat_pt, Point):
                feat_pt = feat_pt.centroid

            nearest_nid = _nearest_node(feat_pt, node_idx)
            if nearest_nid in node_to_hub:
                hub_val, cost = node_to_hub[nearest_nid]
                hub_ids.append(hub_val)
                costs.append(cost)
                allocated_flags.append(True)
            else:
                hub_ids.append(None)
                costs.append(float("nan"))
                allocated_flags.append(False)

        result = gdf.copy()
        result["allocated_hub_id"] = hub_ids
        result["allocation_cost"] = costs
        result["allocated"] = allocated_flags
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "network_gdf": {
                    "type": "object",
                    "description": "Line network GeoDataFrame (arcs).",
                },
                "hubs_gdf": {
                    "type": "object",
                    "description": "Point GeoDataFrame of hubs/NROs.",
                },
                "hub_id_col": {
                    "type": "string",
                    "default": "id",
                    "description": "Column name for hub identifier in hubs_gdf.",
                },
                "max_cost": {
                    "type": ["number", "null"],
                    "description": "Maximum allocation cost. No limit if null.",
                },
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column for arc weight. Defaults to geometric length after metric reprojection.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS for routing. Angular inputs are reprojected here so allocation_cost is in meters. Use EPSG:2154 in France.",
                },
            },
            "required": ["network_gdf", "hubs_gdf"],
        }


@register
class ConnectivityCheckCapability(Capability):
    """Vérifie la connexité d'un réseau (graphe connexe ou non, composantes connexes)."""

    name = "connectivity_check"
    description = (
        "Checks whether a line network forms a connected graph and returns "
        "connected component statistics."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        weight_col: str | None = None,
        return_components: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:               Réseau de lignes.
            weight_col:        Colonne de poids (utilisée uniquement pour construire
                               le graphe, pas pour la connexité).
            return_components: Si True, retourne une ligne par composante connexe
                               avec son emprise (convex hull) et son nombre d'arcs.
                               Si False (défaut), retourne une seule ligne de synthèse.

        Returns:
            GeoDataFrame avec les colonnes :
            - ``is_connected``       : True si le graphe est entièrement connexe.
            - ``n_components``       : Nombre de composantes connexes.
            - ``n_nodes``            : Nombre de nœuds du graphe.
            - ``n_edges``            : Nombre d'arcs du graphe.
            - ``largest_component``  : Nombre d'arcs dans la plus grande composante.
            Si return_components=True, une ligne par composante avec ``component_id``,
            ``n_edges``, et la géométrie (convex hull des arcs de la composante).
        """
        check_tier("pro")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("ConnectivityCheckCapability requires 'networkx'.") from exc

        if gdf.empty:
            return gpd.GeoDataFrame(
                [
                    {
                        "is_connected": None,
                        "n_components": 0,
                        "n_nodes": 0,
                        "n_edges": 0,
                        "largest_component": 0,
                        "geometry": None,
                    }
                ],
                crs=gdf.crs,
            )

        G, node_idx = _build_graph(gdf, weight_col)

        is_connected = nx.is_connected(G)
        components = list(nx.connected_components(G))
        n_components = len(components)
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()

        # Taille de la plus grande composante (en arcs)
        comp_sizes = []
        for comp_nodes in components:
            subgraph = G.subgraph(comp_nodes)
            comp_sizes.append(subgraph.number_of_edges())
        largest_component = max(comp_sizes) if comp_sizes else 0

        if not return_components:
            from shapely.ops import unary_union
            all_geoms = [
                data["geometry"]
                for _, _, data in G.edges(data=True)
                if data.get("geometry") is not None
            ]
            hull = unary_union(all_geoms).convex_hull if all_geoms else None
            return gpd.GeoDataFrame(
                [
                    {
                        "is_connected": is_connected,
                        "n_components": n_components,
                        "n_nodes": n_nodes,
                        "n_edges": n_edges,
                        "largest_component": largest_component,
                        "geometry": hull,
                    }
                ],
                crs=gdf.crs,
            )

        # Mode détaillé : une ligne par composante
        from shapely.ops import unary_union

        rows = []
        for comp_id, comp_nodes in enumerate(
            sorted(components, key=lambda c: -len(c))
        ):
            subgraph = G.subgraph(comp_nodes)
            comp_geoms = [
                data["geometry"]
                for _, _, data in subgraph.edges(data=True)
                if data.get("geometry") is not None
            ]
            hull = unary_union(comp_geoms).convex_hull if comp_geoms else None
            rows.append(
                {
                    "component_id": comp_id,
                    "n_nodes": subgraph.number_of_nodes(),
                    "n_edges": subgraph.number_of_edges(),
                    "is_largest": comp_id == 0,
                    "geometry": hull,
                }
            )

        return gpd.GeoDataFrame(rows, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column for arc weight (for graph construction only).",
                },
                "return_components": {
                    "type": "boolean",
                    "default": False,
                    "description": "If True, return one row per connected component.",
                },
            },
        }


# ---------------------------------------------------------------------------
# OD matrix & minimum spanning tree
# ---------------------------------------------------------------------------


def _node_coords(G, n: int) -> tuple[float, float]:
    """Return (x, y) coordinates of node *n*."""
    data = G.nodes[n]
    return data.get("x", 0.0), data.get("y", 0.0)


def _reconstruct_path_geom(G, node_path: list[int]) -> "LineString":
    """Assemble the geometry of a node path by stitching edge geometries."""
    coords: list[tuple[float, float]] = []
    for i in range(len(node_path) - 1):
        u, v = node_path[i], node_path[i + 1]
        data = G.get_edge_data(u, v, default={})
        geom = data.get("geometry")
        if geom is not None:
            seg = list(geom.coords)
            u_xy = _node_coords(G, u)
            if seg and (
                abs(seg[0][0] - u_xy[0]) > 1e-9 or abs(seg[0][1] - u_xy[1]) > 1e-9
            ):
                seg = list(reversed(seg))
            if coords and seg and coords[-1] == seg[0]:
                coords.extend(seg[1:])
            else:
                coords.extend(seg)
        else:
            coords.append(_node_coords(G, u))
            coords.append(_node_coords(G, v))
    if len(coords) < 2:
        return LineString()
    return LineString(coords)


@register
class ODMatrixCapability(Capability):
    """Origin-destination matrix on a line network (shortest-path distances)."""

    name = "od_matrix"
    description = (
        "Computes the shortest-path cost between every pair of points in an "
        "origins and destinations layer along a line network. "
        "Returns a long-format table."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdf: gpd.GeoDataFrame | None = None,
        destinations_gdf: gpd.GeoDataFrame | None = None,
        weight_col: str | None = None,
        origin_id_col: str = "id",
        destination_id_col: str = "id",
        max_distance: float | None = None,
        include_missing: bool = False,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:                Network layer (LineStrings).
            ref_gdf:            Origins point layer (via ``ref_layer``).
            destinations_gdf:   Destinations point layer. Defaults to origins
                                (symmetric matrix). Inject via ``destinations_layer``.
            weight_col:         Arc weight column (seconds, meters, …).
                                Defaults to geometric length.
            origin_id_col:      Id column on origins.
            destination_id_col: Id column on destinations.
            max_distance:       Drop rows with cost > this threshold.
            include_missing:    Emit rows with cost=None for unreachable
                                pairs when True; otherwise drop them.

        Returns:
            GeoDataFrame with columns origin_id, destination_id, cost, n_edges,
            geometry (path LineString or origin Point).
        """
        check_tier("pro")

        if ref_gdf is None or ref_gdf.empty:
            raise ValueError("od_matrix requires an origins layer (ref_layer).")
        dest_gdf = destinations_gdf if destinations_gdf is not None else ref_gdf

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("networkx required for od_matrix.") from exc

        G, node_idx = _build_graph(gdf, weight_col=weight_col)

        origin_nodes = [_nearest_node(Point(g.x, g.y) if g.geom_type == "Point" else g.centroid, node_idx) for g in ref_gdf.geometry]
        dest_nodes = [_nearest_node(Point(g.x, g.y) if g.geom_type == "Point" else g.centroid, node_idx) for g in dest_gdf.geometry]

        origin_ids = (
            list(ref_gdf[origin_id_col])
            if origin_id_col in ref_gdf.columns
            else list(range(len(ref_gdf)))
        )
        dest_ids = (
            list(dest_gdf[destination_id_col])
            if destination_id_col in dest_gdf.columns
            else list(range(len(dest_gdf)))
        )

        symmetric = ref_gdf is dest_gdf
        rows: list[dict[str, Any]] = []
        for i, src in enumerate(origin_nodes):
            try:
                lengths, paths = nx.single_source_dijkstra(
                    G, src, weight="weight", cutoff=max_distance
                )
            except nx.NodeNotFound:
                lengths, paths = {}, {}

            for j, dst in enumerate(dest_nodes):
                if symmetric and i == j:
                    if include_missing:
                        rows.append({
                            "origin_id": origin_ids[i],
                            "destination_id": dest_ids[j],
                            "cost": 0.0,
                            "n_edges": 0,
                            "geometry": ref_gdf.geometry.iloc[i],
                        })
                    continue

                if dst in lengths:
                    node_path = paths[dst]
                    line = _reconstruct_path_geom(G, node_path)
                    rows.append({
                        "origin_id": origin_ids[i],
                        "destination_id": dest_ids[j],
                        "cost": float(lengths[dst]),
                        "n_edges": max(len(node_path) - 1, 0),
                        "geometry": line if not line.is_empty else ref_gdf.geometry.iloc[i],
                    })
                elif include_missing:
                    rows.append({
                        "origin_id": origin_ids[i],
                        "destination_id": dest_ids[j],
                        "cost": None,
                        "n_edges": 0,
                        "geometry": ref_gdf.geometry.iloc[i],
                    })

        if not rows:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)
        return gpd.GeoDataFrame(rows, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layer": {
                    "type": "string",
                    "description": "Origins layer (points).",
                },
                "destinations_layer": {
                    "type": ["string", "null"],
                    "description": "Destinations layer. None = origins (symmetric).",
                },
                "weight_col": {"type": ["string", "null"]},
                "origin_id_col": {"type": "string", "default": "id"},
                "destination_id_col": {"type": "string", "default": "id"},
                "max_distance": {"type": ["number", "null"]},
                "include_missing": {"type": "boolean", "default": False},
            },
            # ``ref_layer`` is pipeline plumbing (stripped by
            # rules.validation._PLUMBING_KEYS before validation) so it cannot
            # appear in ``required`` — the runtime raises a clear ValueError
            # when ``ref_gdf`` is None, which preserves the contract.
        }


@register
class MinimumSpanningTreeCapability(Capability):
    """Minimum spanning tree of a line network (Kruskal)."""

    name = "mst"
    description = (
        "Computes the minimum spanning tree of a line network and returns "
        "the MST edges as a LineString GeoDataFrame."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        weight_col: str | None = None,
        **_,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Input network layer (LineStrings).
            weight_col: Arc weight column. Defaults to geometric length.
        """
        check_tier("pro")

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("networkx required for mst.") from exc

        if gdf.empty:
            return gdf.copy()

        G, _ = _build_graph(gdf, weight_col=weight_col)
        if G.number_of_edges() == 0:
            return gpd.GeoDataFrame(geometry=[], crs=gdf.crs)

        mst = nx.minimum_spanning_tree(G, weight="weight")

        rows: list[dict[str, Any]] = []
        for u, v, data in mst.edges(data=True):
            geom = data.get("geometry")
            if geom is None:
                geom = LineString([_node_coords(G, u), _node_coords(G, v)])
            rows.append({
                "u": int(u),
                "v": int(v),
                "weight": float(data.get("weight", geom.length)),
                "geometry": geom,
            })

        return gpd.GeoDataFrame(rows, crs=gdf.crs)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Arc weight column; defaults to geometric length.",
                },
            },
        }
