"""Network resilience capabilities for GISPulse.

Requires optional dependencies:
    - networkx  (via :class:`~gispulse.core.network_graph_handle.NetworkGraph`,
      ``disjoint_paths`` only — ``network_bridges`` is dependency-free)
    - shapely   (already a geopandas dependency)

Two primitives for survivability analysis of line networks, ported from the
graph core of an internal fibre-network project (SPOF audits and protection
proofs), stripped of any business logic:

* ``disjoint_paths`` — minimum **total**-cost set of *k* node- or
  edge-disjoint paths between two points (Suurballe: successive shortest
  paths with Johnson potentials on a split-node residual graph). This is the
  primitive behind "is this site protected against a single failure?": a
  second disjoint path existing means no single node/edge failure can cut the
  route. It finds pairs a naive "remove the first path, search again" scan
  provably misses.
* ``network_bridges`` — tags every line whose removal disconnects its
  component (bridge / cut-edge = structural SPOF), iterative Tarjan.
  Parallel lines between the same two nodes are never bridges.

Like every capability in :mod:`gispulse.capabilities.network` both require
``tier="pro"``.
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Iterator

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, Point

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import register
from gispulse.core.crs import is_angular
from gispulse.core.network_graph_handle import NetworkGraph
from gispulse.persistence.tier import check_tier

# Column contract of the emitted disjoint-path edges. Module constant so the
# empty-result branches and the populated branch stay in sync.
_DISJOINT_COLUMNS = [
    "path_id",
    "path_order",
    "edge_weight",
    "path_cost",
    "paths_found",
    "geometry",
]

# Residual-arc kinds (see _residual_arcs).
_ARC_EDGE = 0  # traverses / cancels an original network edge
_ARC_NODE = 1  # traverses / cancels a split-node arc (node-disjoint mode)


def _split_ids(node: int, node_disjoint: bool) -> tuple[int, int]:
    """Return ``(v_in, v_out)`` ids of *node* in the split graph.

    Node-disjoint mode splits every node ``v`` into ``v_in = 2v`` and
    ``v_out = 2v + 1`` joined by a capacity-1 zero-weight arc, which is what
    makes interior nodes single-use. Edge-disjoint mode leaves nodes whole
    (``v_in == v_out == v``).
    """
    if node_disjoint:
        return 2 * node, 2 * node + 1
    return node, node


def _residual_arcs(
    edges: dict[int, tuple[int, int, float]],
    eflow: dict[int, int],
    nused: dict[int, bool],
    *,
    node_disjoint: bool,
    source: int,
    target: int,
) -> dict[int, list[tuple[int, float, int, tuple[int, int]]]]:
    """Materialize the residual split-graph arcs for the current flow.

    Returns ``{tail: [(head, weight, kind, ref), ...]}`` with per-tail lists
    sorted for determinism. ``ref`` is ``(edge_id, direction)`` for edge arcs
    (direction ±1 relative to the stored ``(u, v)`` orientation) and
    ``(node, 0)`` for split arcs. Cancellation arcs carry the negated weight;
    the caller runs Dijkstra on Johnson-reduced costs so negativity is safe.

    The source and target are never split (their split arc would bound the
    number of paths at 1): arcs leave from ``source_out`` and arrive at
    ``target_in`` directly.
    """
    arcs: dict[int, list[tuple[int, float, int, tuple[int, int]]]] = {}

    def _add(tail: int, head: int, weight: float, kind: int, ref: tuple[int, int]) -> None:
        arcs.setdefault(tail, []).append((head, weight, kind, ref))

    for eid in sorted(edges):
        u, v, w = edges[eid]
        flow = eflow[eid]
        u_in, u_out = _split_ids(u, node_disjoint)
        v_in, v_out = _split_ids(v, node_disjoint)
        if flow == 0:
            _add(u_out, v_in, w, _ARC_EDGE, (eid, +1))
            _add(v_out, u_in, w, _ARC_EDGE, (eid, -1))
        elif flow == +1:  # flowing u -> v; only the cancellation arc remains
            _add(v_in, u_out, -w, _ARC_EDGE, (eid, -1))
        else:  # flow == -1, flowing v -> u
            _add(u_in, v_out, -w, _ARC_EDGE, (eid, +1))

    if node_disjoint:
        for node in sorted(nused):
            v_in, v_out = _split_ids(node, node_disjoint)
            if v_in in (source, target) or v_out in (source, target):
                continue
            if nused[node]:
                _add(v_out, v_in, 0.0, _ARC_NODE, (node, 0))
            else:
                _add(v_in, v_out, 0.0, _ARC_NODE, (node, 0))

    for lst in arcs.values():
        lst.sort()
    return arcs


def _dijkstra_reduced(
    arcs: dict[int, list[tuple[int, float, int, tuple[int, int]]]],
    source: int,
    pot: dict[int, float],
) -> tuple[dict[int, float], dict[int, tuple[int, int, tuple[int, int]]]]:
    """Dijkstra on Johnson-reduced costs ``w + pot[a] - pot[b]``.

    Reduced costs are non-negative by the successive-shortest-path invariant;
    float dust from the ``+w`` / ``-w`` cancellation pairs is clamped at 0 so
    the heap never sees a negative key. Nodes absent from *pot* were
    unreachable in a previous round and stay unreachable (residual reversals
    never extend reachability), so their arcs are skipped.

    Runs to exhaustion — no early exit at any target. The caller updates the
    Johnson potentials with the returned distances and drops the unreached
    nodes as unreachable-forever, which is only sound when every reachable
    node has been *finalized*: an early break would leave relaxed-but-not-
    popped nodes out of ``dist`` and silently amputate later rounds.

    Returns ``(dist, pred)`` in reduced units; ``pred[b] = (a, kind, ref)``.
    """
    dist: dict[int, float] = {source: 0.0}
    pred: dict[int, tuple[int, int, tuple[int, int]]] = {}
    heap: list[tuple[float, int]] = [(0.0, source)]
    done: set[int] = set()
    while heap:
        d, a = heapq.heappop(heap)
        if a in done:
            continue
        done.add(a)
        pot_a = pot.get(a)
        if pot_a is None:
            continue
        for b, w, kind, ref in arcs.get(a, ()):
            pot_b = pot.get(b)
            if pot_b is None or b in done:
                continue
            reduced = w + pot_a - pot_b
            if reduced < 0.0:  # float dust only — clamp, never a real gain
                reduced = 0.0
            nd = d + reduced
            if nd < dist.get(b, math.inf):
                dist[b] = nd
                pred[b] = (a, kind, ref)
                heapq.heappush(heap, (nd, b))
    return dist, pred


def _suurballe_disjoint_paths(
    edges: dict[int, tuple[int, int, float]],
    adjacency_nodes: list[int],
    s: int,
    t: int,
    k: int,
    *,
    node_disjoint: bool,
) -> list[tuple[list[int], list[int]]]:
    """Min total-cost *k* disjoint paths ``s -> t`` (Suurballe / SSP).

    Args:
        edges: ``{edge_id: (u, v, weight)}`` undirected simple edges,
            ``weight >= 0``.
        adjacency_nodes: every node id appearing in *edges* (plus s, t).
        s / t: distinct endpoint node ids.
        k: number of disjoint paths wanted (>= 1).
        node_disjoint: interior nodes single-use when True, else only edges.

    Returns:
        Up to *k* ``(node_sequence, edge_id_sequence)`` paths — fewer when the
        graph cannot provide more (the shortfall is the SPOF signal, not an
        error). The set minimizes the **sum** of path costs for its size.
    """
    _, source = _split_ids(s, node_disjoint)
    target, _ = _split_ids(t, node_disjoint)
    eflow: dict[int, int] = {eid: 0 for eid in edges}
    nused: dict[int, bool] = {n: False for n in adjacency_nodes if n not in (s, t)}

    # Initial potentials = shortest distance from source in the flow-free
    # residual (all weights >= 0, so plain Dijkstra with zero potentials).
    arcs = _residual_arcs(
        edges, eflow, nused, node_disjoint=node_disjoint, source=source, target=target
    )
    zero_pot: dict[int, float] = {}
    for tail, lst in arcs.items():
        zero_pot[tail] = 0.0
        for head, _w, _kind, _ref in lst:
            zero_pot[head] = 0.0
    zero_pot.setdefault(source, 0.0)
    zero_pot.setdefault(target, 0.0)
    dist, _ = _dijkstra_reduced(arcs, source, zero_pot)
    pot = dict(dist)  # unreached nodes drop out (unreachable forever)
    if target not in pot:
        return []

    found = 0
    for _round in range(k):
        arcs = _residual_arcs(
            edges, eflow, nused, node_disjoint=node_disjoint, source=source, target=target
        )
        dist, pred = _dijkstra_reduced(arcs, source, pot)
        if target not in dist:
            break
        # Augment along the predecessor chain.
        node = target
        while node != source:
            a, kind, ref = pred[node]
            if kind == _ARC_EDGE:
                eid, direction = ref
                eflow[eid] += direction  # 0 -> ±1, or ∓1 -> 0 (cancellation)
            else:
                nused[ref[0]] = not nused[ref[0]]
            node = a
        found += 1
        # Johnson potential update; nodes unreached this round leave the game.
        pot = {n: pot[n] + d for n, d in dist.items() if n in pot}

    if found == 0:
        return []

    # Decompose the flow into `found` paths, deterministically (smallest
    # next-node first). Node-disjoint flows have a unique successor at every
    # interior node; edge-disjoint flows may branch at shared nodes, where the
    # smallest-id choice keeps the output stable. A step budget guards the
    # walk against degenerate zero-weight flow cycles.
    out_arcs: dict[int, list[tuple[int, int]]] = {}
    flowed = 0
    for eid in sorted(edges):
        u, v, _w = edges[eid]
        if eflow[eid] == +1:
            out_arcs.setdefault(u, []).append((v, eid))
            flowed += 1
        elif eflow[eid] == -1:
            out_arcs.setdefault(v, []).append((u, eid))
            flowed += 1
    for lst in out_arcs.values():
        lst.sort()

    paths: list[tuple[list[int], list[int]]] = []
    for _p in range(found):
        nodes = [s]
        eids: list[int] = []
        cur = s
        steps = 0
        while cur != t:
            steps += 1
            if steps > flowed:
                raise RuntimeError(
                    "disjoint_paths: flow decomposition exceeded its step "
                    "budget (degenerate zero-weight cycle in the flow)."
                )
            nxt, eid = out_arcs[cur].pop(0)
            nodes.append(nxt)
            eids.append(eid)
            cur = nxt
        paths.append((nodes, eids))

    def _cost(path: tuple[list[int], list[int]]) -> tuple[float, list[int]]:
        return (sum(edges[eid][2] for eid in path[1]), path[0])

    paths.sort(key=_cost)
    return paths


@register
class DisjointPathsCapability(Capability):
    """K chemins disjoints de coût total minimal entre deux points (Suurballe)."""

    name = "disjoint_paths"
    description = (
        "Finds k mutually disjoint paths of minimum total cost between two "
        "points through a line network (Suurballe — successive shortest "
        "paths on a split-node residual graph). mode='node' forbids sharing "
        "interior nodes, mode='edge' only forbids sharing edges. Returns the "
        "arcs of every path found; fewer than k paths (paths_found column) "
        "is the single-point-of-failure signal, not an error. Finds pairs "
        "that a naive remove-first-path-then-retry scan misses."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        start_x: float = 0.0,
        start_y: float = 0.0,
        end_x: float = 0.0,
        end_y: float = 0.0,
        k: int = 2,
        mode: str = "node",
        weight_col: str | None = None,
        crs_meters: str | None = None,
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:        Réseau de lignes (LineString / MultiLineString).
            start_x:    X/longitude du départ (CRS du ``gdf``).
            start_y:    Y/latitude du départ.
            end_x:      X/longitude de l'arrivée.
            end_y:      Y/latitude de l'arrivée.
            k:          Nombre de chemins disjoints recherchés (>= 1, défaut 2 —
                        le contrat protection/SPOF classique).
            mode:       ``"node"`` (défaut) : les chemins ne partagent aucun
                        nœud intérieur (protection maximale) ; ``"edge"`` :
                        seuls les arcs sont à usage unique, un nœud peut être
                        partagé.
            weight_col: Colonne de poids des arcs ; longueur géométrique après
                        reprojection métrique si absente.
            crs_meters: CRS métrique de travail quand le réseau est angulaire
                        (défaut EPSG:3857).

        Returns:
            GeoDataFrame des arcs de chaque chemin trouvé : ``path_id``
            (0..paths_found-1, trié par coût de chemin croissant),
            ``path_order`` (rang de l'arc dans son chemin), ``edge_weight``,
            ``path_cost`` (coût total du chemin), ``paths_found`` (nombre de
            chemins trouvés, répété sur chaque ligne — < k signale un SPOF),
            ``geometry``. Reprojeté vers le CRS d'origine. Vide si aucune
            route n'existe.

        Raises:
            ValueError: ``k < 1``, ``mode`` inconnu, départ et arrivée
                s'accrochant au même nœud du réseau, ou poids d'arc NaN /
                négatif (garde qualité de données ; ``inf`` est autorisé).

        Note:
            Le graphe est simple : des lignes parallèles entre les deux mêmes
            nœuds sont fusionnées (la dernière gagne) — elles n'apportent pas
            de second chemin en mode ``edge``.
        """
        check_tier("pro")

        if k < 1:
            raise ValueError(f"disjoint_paths: k must be >= 1 (got {k}).")
        if mode not in ("node", "edge"):
            raise ValueError(
                f"disjoint_paths: mode must be 'node' or 'edge' (got {mode!r})."
            )

        original_crs = gdf.crs
        if gdf.empty:
            return gpd.GeoDataFrame(columns=_DISJOINT_COLUMNS, crs=original_crs)

        reproject = is_angular(gdf)
        effective_crs = crs_meters or "EPSG:3857"
        if reproject:
            network_m = gdf.to_crs(effective_crs)
            src_pt = (
                gpd.GeoSeries([Point(start_x, start_y)], crs=original_crs)
                .to_crs(effective_crs)
                .iloc[0]
            )
            dst_pt = (
                gpd.GeoSeries([Point(end_x, end_y)], crs=original_crs)
                .to_crs(effective_crs)
                .iloc[0]
            )
        else:
            network_m = gdf
            src_pt = Point(start_x, start_y)
            dst_pt = Point(end_x, end_y)

        graph = NetworkGraph.from_lines(network_m, weight_col)
        G = graph.graph
        if len(graph) == 0:
            return gpd.GeoDataFrame(columns=_DISJOINT_COLUMNS, crs=original_crs)

        s = graph.nearest_node(src_pt)
        t = graph.nearest_node(dst_pt)
        if s == t:
            raise ValueError(
                "disjoint_paths: start and end snap to the same network node "
                "— disjointness is undefined for a zero-length route."
            )

        # NaN / negative fast-fail (AX #5): NaN breaks ordering, a negative
        # weight breaks the successive-shortest-path optimality invariant.
        edges: dict[int, tuple[int, int, float]] = {}
        for eid, (u, v) in enumerate(sorted(G.edges())):
            w = float(G.edges[u, v]["weight"])
            if math.isnan(w):
                raise ValueError(
                    "disjoint_paths: NaN edge weight — NaN costs break the "
                    "deterministic path order (inf is allowed)."
                )
            if w < 0.0:
                raise ValueError(
                    "disjoint_paths: negative edge weight — Suurballe "
                    "requires non-negative arc costs."
                )
            a, b = (u, v) if u <= v else (v, u)
            edges[eid] = (a, b, w)

        paths = _suurballe_disjoint_paths(
            edges,
            list(G.nodes()),
            s,
            t,
            k,
            node_disjoint=(mode == "node"),
        )
        if not paths:
            return gpd.GeoDataFrame(columns=_DISJOINT_COLUMNS, crs=original_crs)

        rows: list[dict[str, Any]] = []
        for path_id, (nodes, eids) in enumerate(paths):
            path_cost = sum(edges[eid][2] for eid in eids)
            for order, eid in enumerate(eids):
                a, b, w = edges[eid]
                geom = G.edges[a, b].get("geometry")
                if geom is None:
                    geom = LineString([graph.node_point(a), graph.node_point(b)])
                rows.append(
                    {
                        "path_id": path_id,
                        "path_order": order,
                        "edge_weight": w,
                        "path_cost": path_cost,
                        "paths_found": len(paths),
                        "geometry": geom,
                    }
                )

        result = gpd.GeoDataFrame(rows, geometry="geometry", crs=network_m.crs)
        if reproject and original_crs is not None:
            result = result.to_crs(original_crs)
        return result.reset_index(drop=True)

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start_x": {"type": "number"},
                "start_y": {"type": "number"},
                "end_x": {"type": "number"},
                "end_y": {"type": "number"},
                "k": {
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "description": "Number of disjoint paths wanted (2 = classic protection pair).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["node", "edge"],
                    "default": "node",
                    "description": "'node': no shared interior node; 'edge': only edges are single-use.",
                },
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column for arc weight. If null, geometric length after metric reprojection.",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS used to reproject an angular network. Use EPSG:2154 in France.",
                },
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        }


def _iter_line_parts(geom: Any) -> Iterator[LineString]:
    """Yield the LineString parts of a (Multi)LineString geometry."""
    if geom is None or geom.is_empty:
        return
    parts = geom.geoms if isinstance(geom, MultiLineString) else [geom]
    for part in parts:
        if isinstance(part, LineString) and len(part.coords) >= 2:
            yield part


def _find_bridge_edge_ids(
    n_nodes: int,
    adjacency: list[list[tuple[int, int]]],
) -> set[int]:
    """Bridge (cut-edge) ids of an undirected multigraph — iterative Tarjan.

    Faithful port of the fibre project's ``find_bridges``: the adjacency
    carries edge ids so a parallel edge (same node pair, distinct id) is
    never a bridge — only the exact parent edge is excluded when looking
    back up the DFS tree. Iterative (explicit frame stack), so deep networks
    cannot blow the recursion limit.
    """
    disc = [-1] * n_nodes
    low = [0] * n_nodes
    bridges: set[int] = set()
    timer = 0

    for root in range(n_nodes):
        if disc[root] != -1:
            continue
        stack: list[tuple[int, int, int]] = [(root, -1, 0)]  # node, parent edge id, next idx
        disc[root] = low[root] = timer
        timer += 1
        while stack:
            node, pedge, idx = stack[-1]
            if idx < len(adjacency[node]):
                stack[-1] = (node, pedge, idx + 1)
                nxt, eid = adjacency[node][idx]
                if eid == pedge:
                    continue  # never climb back through the exact parent edge
                if disc[nxt] == -1:
                    disc[nxt] = low[nxt] = timer
                    timer += 1
                    stack.append((nxt, eid, 0))
                else:
                    low[node] = min(low[node], disc[nxt])
            else:
                stack.pop()
                if stack:
                    parent = stack[-1][0]
                    low[parent] = min(low[parent], low[node])
                    if low[node] > disc[parent]:
                        bridges.add(pedge)
    return bridges


@register
class NetworkBridgesCapability(Capability):
    """Marque les ponts (arêtes de coupure = SPOF structurels) d'un réseau de lignes."""

    name = "network_bridges"
    description = (
        "Tags every line whose removal disconnects its component (bridge / "
        "cut-edge — the structural single points of failure of the network). "
        "Iterative Tarjan on the endpoint graph; parallel lines between the "
        "same two nodes are never bridges. Adds a boolean 'is_bridge' column. "
        "A connected network with no bridge is 2-edge-connected (survives any "
        "single line failure)."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        snap_decimals: int = 6,
        bridge_col: str = "is_bridge",
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:           Réseau de lignes (LineString / MultiLineString).
            snap_decimals: Arrondi de coordonnées fusionnant les extrémités
                           coïncidentes (défaut 6, la convention
                           :class:`NetworkGraph`).
            bridge_col:    Nom de la colonne booléenne ajoutée.

        Returns:
            Copie du ``gdf`` avec *bridge_col* : True quand la suppression de
            la ligne déconnecte sa composante. Purement topologique : aucune
            reprojection nécessaire. Une ligne multi-parties est marquée pont
            si **au moins une** de ses parties est un pont (les parties sont
            des arêtes indépendantes du graphe). Géométrie vide/nulle ou
            boucle sur soi-même => False.
        """
        check_tier("pro")

        node_index: dict[tuple[float, float], int] = {}
        adjacency: list[list[tuple[int, int]]] = []
        part_row: list[int] = []  # part edge id -> row position

        def _node(x: float, y: float) -> int:
            key = (round(x, snap_decimals), round(y, snap_decimals))
            nid = node_index.get(key)
            if nid is None:
                nid = len(node_index)
                node_index[key] = nid
                adjacency.append([])
            return nid

        for pos, geom in enumerate(gdf.geometry):
            for part in _iter_line_parts(geom):
                coords = list(part.coords)
                u = _node(*coords[0][:2])
                v = _node(*coords[-1][:2])
                if u == v:
                    continue  # a self-loop is never a bridge
                eid = len(part_row)
                part_row.append(pos)
                adjacency[u].append((v, eid))
                adjacency[v].append((u, eid))

        bridge_ids = _find_bridge_edge_ids(len(adjacency), adjacency)
        flags = [False] * len(gdf)
        for eid in bridge_ids:
            flags[part_row[eid]] = True

        result = gdf.copy()
        result[bridge_col] = flags
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "snap_decimals": {
                    "type": "integer",
                    "default": 6,
                    "description": "Coordinate rounding fusing coincident endpoints.",
                },
                "bridge_col": {
                    "type": "string",
                    "default": "is_bridge",
                    "description": "Name of the boolean output column.",
                },
            },
        }


@register
class NetworkRedundancyCapability(Capability):
    """Compte, par site, les routes disjointes (plafonnées à k) vers des installations."""

    name = "network_redundancy"
    description = (
        "Per-site redundancy audit: for every input point, counts the "
        "mutually disjoint routes (capped at k, default 2) through a line "
        "network to a set of facility points — 0 = unreachable, 1 = single "
        "point of failure, k = protected. ref_layers must list "
        "[network_alias, facilities_alias] in that order. Uses the same "
        "Suurballe engine as 'disjoint_paths' (mode='node' by default), so "
        "a site is protected exactly when two genuinely disjoint arms reach "
        "a facility — pairs a naive remove-first-path scan misses are found."
    )

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        ref_gdfs: list[gpd.GeoDataFrame] | None = None,
        k: int = 2,
        mode: str = "node",
        weight_col: str | None = None,
        redundancy_col: str = "redundancy",
        crs_meters: str | None = None,
        **_: Any,
    ) -> gpd.GeoDataFrame:
        """
        Args:
            gdf:            Sites à auditer (points ; les géométries non
                            ponctuelles utilisent leur centroïde). La sortie
                            est une copie annotée de cette couche.
            ref_gdfs:       ``[réseau, installations]`` injectés via
                            ``ref_layers``, dans cet ordre : le réseau de
                            lignes traversé, puis les points d'installation
                            (sources/objectifs de rattachement). Les deux
                            sont requis.
            k:              Plafond du comptage (>= 1, défaut 2 — le contrat
                            protection classique : 2 = protégé).
            mode:           ``"node"`` (défaut) : les routes ne partagent
                            aucun nœud intérieur ; ``"edge"`` : seuls les
                            arcs sont à usage unique.
            weight_col:     Colonne de poids des arcs du réseau ; longueur
                            géométrique sinon.
            redundancy_col: Nom de la colonne entière ajoutée.
            crs_meters:     CRS métrique de travail quand le réseau est
                            angulaire (défaut EPSG:3857).

        Returns:
            Copie du ``gdf`` avec *redundancy_col* : nombre de routes
            disjointes trouvées vers la meilleure installation, plafonné à
            ``k``. ``0`` = aucune installation atteignable, ``1`` = une seule
            route (SPOF), ``k`` = protégé. Un site posé sur une installation
            vaut ``k``. Sites sans géométrie => ``0``.

        Raises:
            ValueError: couches réseau/installations absentes, ``k < 1``,
                ``mode`` inconnu, ou poids d'arc NaN / négatif.
        """
        check_tier("pro")

        if k < 1:
            raise ValueError(f"network_redundancy: k must be >= 1 (got {k}).")
        if mode not in ("node", "edge"):
            raise ValueError(
                f"network_redundancy: mode must be 'node' or 'edge' (got {mode!r})."
            )
        if ref_gdfs is None or len(ref_gdfs) < 2:
            raise ValueError(
                "network_redundancy requires ref_layers=[network_alias, "
                "facilities_alias] (two layers, in that order)."
            )
        network_gdf, facilities_gdf = ref_gdfs[0], ref_gdfs[1]
        if facilities_gdf is None or facilities_gdf.empty:
            raise ValueError(
                "network_redundancy: the facilities layer (ref_layers[1]) is empty."
            )

        result = gdf.copy()
        if gdf.empty:
            result[redundancy_col] = []
            return result
        if network_gdf is None or network_gdf.empty:
            result[redundancy_col] = 0
            return result

        try:
            import networkx as nx
        except ImportError as exc:
            raise ImportError("NetworkRedundancyCapability requires 'networkx'.") from exc

        reproject = is_angular(network_gdf)
        effective_crs = crs_meters or "EPSG:3857"
        network_m = network_gdf.to_crs(effective_crs) if reproject else network_gdf
        working_crs = network_m.crs

        def _align(layer: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            if layer.crs is not None and layer.crs != working_crs:
                return layer.to_crs(working_crs)
            return layer

        sites_m = _align(gdf)
        facilities_m = _align(facilities_gdf)

        graph = NetworkGraph.from_lines(network_m, weight_col)
        G = graph.graph
        if len(graph) == 0:
            result[redundancy_col] = 0
            return result

        # NaN / negative fast-fail — same data-quality guard as disjoint_paths.
        edges: dict[int, tuple[int, int, float]] = {}
        for eid, (u, v) in enumerate(sorted(G.edges())):
            w = float(G.edges[u, v]["weight"])
            if math.isnan(w):
                raise ValueError(
                    "network_redundancy: NaN edge weight — NaN costs break "
                    "the deterministic route order (inf is allowed)."
                )
            if w < 0.0:
                raise ValueError(
                    "network_redundancy: negative edge weight — Suurballe "
                    "requires non-negative arc costs."
                )
            a, b = (u, v) if u <= v else (v, u)
            edges[eid] = (a, b, w)
        all_nodes = list(G.nodes())

        # Facility nodes (nearest snap, deduplicated) grouped by component so
        # unreachable facilities are skipped without a Suurballe run.
        facility_nodes: set[int] = set()
        for geom in facilities_m.geometry:
            if geom is None or geom.is_empty:
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid
            nid = graph.nearest_node(pt)
            if nid >= 0:
                facility_nodes.add(nid)
        component_of: dict[int, int] = {}
        for comp_id, comp in enumerate(nx.connected_components(G)):
            for node in comp:
                component_of[node] = comp_id
        facilities_by_component: dict[int, list[int]] = {}
        for nid in sorted(facility_nodes):
            facilities_by_component.setdefault(component_of[nid], []).append(nid)

        node_disjoint = mode == "node"
        counts: list[int] = []
        # Same-node results are memoized: co-located sites share one audit.
        memo: dict[int, int] = {}
        for geom in sites_m.geometry:
            if geom is None or geom.is_empty:
                counts.append(0)
                continue
            pt = geom if geom.geom_type == "Point" else geom.centroid
            site_node = graph.nearest_node(pt)
            cached = memo.get(site_node)
            if cached is not None:
                counts.append(cached)
                continue
            if site_node in facility_nodes:
                count = k
            else:
                reachable = facilities_by_component.get(component_of[site_node], [])
                count = 0
                for facility in reachable:
                    found = len(
                        _suurballe_disjoint_paths(
                            edges,
                            all_nodes,
                            site_node,
                            facility,
                            k,
                            node_disjoint=node_disjoint,
                        )
                    )
                    count = max(count, found)
                    if count >= k:
                        break
            memo[site_node] = count
            counts.append(count)

        result[redundancy_col] = counts
        return result

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "ref_layers": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": (
                        "[network_alias, facilities_alias] in that order — "
                        "the line network, then the facility points."
                    ),
                },
                "k": {
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "description": "Count cap (2 = classic protected/SPOF contract).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["node", "edge"],
                    "default": "node",
                    "description": "'node': no shared interior node; 'edge': only edges are single-use.",
                },
                "weight_col": {
                    "type": ["string", "null"],
                    "description": "Column for arc weight. If null, geometric length after metric reprojection.",
                },
                "redundancy_col": {
                    "type": "string",
                    "default": "redundancy",
                    "description": "Name of the integer output column (0=unreachable, 1=SPOF, k=protected).",
                },
                "crs_meters": {
                    "type": ["string", "null"],
                    "default": None,
                    "description": "Metric CRS used to reproject an angular network. Use EPSG:2154 in France.",
                },
            },
        }
