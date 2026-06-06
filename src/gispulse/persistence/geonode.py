"""GeoNode provider — read and write GeoNode-hosted datasets.

DP1 of the *generic data provider* track. `GeoNode <https://geonode.org>`_
publishes spatial datasets through an embedded GeoServer (OGC WFS/WMS) plus
its own REST API v2. This module wires both directions behind stable URIs:

    geonode://<instance>/<dataset>

**Read** resolves to the instance's GeoServer **WFS** endpoint and reuses
the existing OGC fetcher — ``gispulse.load("geonode://prod/roads")`` returns
a GeoDataFrame with bbox push-down, no GeoNode-specific transport code.

**Write** (:func:`publish`) packages a GeoDataFrame as a GeoPackage and
uploads it through the GeoNode **REST API v2 uploads** endpoint, creating or
replacing the dataset.

Instances are declared programmatically (:meth:`GeoNodeRegistry.register`)
or from the ``GISPULSE_GEONODE`` environment variable (a JSON object). When
the URI authority is not a registered alias it is treated as a bare host
with default paths, so ``geonode://geo.example.org/roads`` works without any
prior declaration.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from gispulse.core.logging import get_logger
from gispulse.core.plugin_model import AccessProtocol, AccessSpec

log = get_logger(__name__)

#: Environment variable carrying a JSON map of GeoNode instance declarations.
GEONODE_ENV = "GISPULSE_GEONODE"

#: URI scheme that addresses a GeoNode dataset.
GEONODE_SCHEME = "geonode"


@dataclass(frozen=True)
class GeoNode:
    """Declaration of one GeoNode instance.

    Args:
        name:           Logical alias used in ``geonode://<name>/…``.
        host:           Base URL of the instance (``https://geo.example.org``).
        geoserver_path: OWS endpoint path under *host* for WFS reads.
        workspace:      GeoServer workspace the datasets live in.
        version:        WFS version requested.
        crs:            CRS requested from the server.
        upload_path:    REST API v2 uploads endpoint path for writes.
        auth:           Token/credential name resolved by the licence layer
                        (or a literal token); forwarded to read/write.
        metadata:       Free-form descriptive metadata.
    """

    name: str
    host: str
    geoserver_path: str = "/geoserver/ows"
    workspace: str = "geonode"
    version: str = "2.0.0"
    crs: str = "EPSG:4326"
    upload_path: str = "/api/v2/uploads"
    auth: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def wfs_endpoint(self) -> str:
        return f"{self.host.rstrip('/')}{self.geoserver_path}"

    def upload_endpoint(self) -> str:
        return f"{self.host.rstrip('/')}{self.upload_path}"

    def typename(self, dataset: str) -> str:
        """The WFS typeName for *dataset* (``<workspace>:<dataset>`` unless qualified)."""
        return dataset if ":" in dataset else f"{self.workspace}:{dataset}"

    def read_access(self, dataset: str) -> AccessSpec:
        """Build the read :class:`AccessSpec` for *dataset* (WFS via GeoServer)."""
        return AccessSpec(
            protocol=AccessProtocol.OGC_FEATURES,
            endpoint=self.wfs_endpoint(),
            params={
                "collection": self.typename(dataset),
                "source_type": "wfs",
                "version": self.version,
                "crs": self.crs,
                **({"auth": self.auth} if self.auth else {}),
            },
        )


class GeoNodeRegistry:
    """Thread-safe registry of declared :class:`GeoNode` instances."""

    def __init__(self) -> None:
        self._nodes: dict[str, GeoNode] = {}
        self._lock = threading.Lock()
        self._env_loaded = False

    def register(self, node: GeoNode, *, override: bool = False) -> None:
        with self._lock:
            if node.name in self._nodes and not override:
                raise ValueError(
                    f"geonode '{node.name}' already registered; "
                    f"pass override=True to replace"
                )
            self._nodes[node.name] = node
        log.debug("geonode_registered", name=node.name, host=node.host)

    def get(self, name: str) -> GeoNode:
        self._ensure_env_loaded()
        try:
            return self._nodes[name]
        except KeyError:
            raise KeyError(
                f"no geonode instance named {name!r} is registered "
                f"(available: {', '.join(self.names()) or 'none'})"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._nodes)

    def clear(self) -> None:
        with self._lock:
            self._nodes.clear()
            self._env_loaded = False

    def resolve(self, authority: str) -> GeoNode:
        """Resolve a URI authority to a :class:`GeoNode`.

        A registered alias wins; otherwise the authority is taken as a bare
        host (``https://<authority>``) with default GeoServer/upload paths.
        """
        self._ensure_env_loaded()
        if authority in self._nodes:
            return self._nodes[authority]
        host = authority if "://" in authority else f"https://{authority}"
        return GeoNode(name=authority, host=host)

    def _ensure_env_loaded(self) -> None:
        if self._env_loaded:
            return
        with self._lock:
            if self._env_loaded:
                return
            self._env_loaded = True
            raw = os.environ.get(GEONODE_ENV, "").strip()
            if not raw:
                return
            try:
                decls = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("geonode_env_invalid_json", error=str(exc))
                return
            if not isinstance(decls, dict):
                log.warning("geonode_env_not_object", got=type(decls).__name__)
                return
            for name, spec in decls.items():
                if name in self._nodes:  # explicit registration wins
                    continue
                if not isinstance(spec, dict) or "host" not in spec:
                    log.warning("geonode_env_bad_decl", name=name)
                    continue
                self._nodes[name] = GeoNode(
                    name=name,
                    host=str(spec["host"]),
                    geoserver_path=str(spec.get("geoserver_path", "/geoserver/ows")),
                    workspace=str(spec.get("workspace", "geonode")),
                    version=str(spec.get("version", "2.0.0")),
                    crs=str(spec.get("crs", "EPSG:4326")),
                    upload_path=str(spec.get("upload_path", "/api/v2/uploads")),
                    auth=spec.get("auth"),
                    metadata=dict(spec.get("metadata") or {}),
                )


def parse_geonode_uri(uri: str) -> tuple[str, str]:
    """Split ``geonode://<instance>/<dataset>`` into ``(instance, dataset)``.

    Raises:
        ValueError: if *uri* is not a well-formed geonode URI.
    """
    scheme, sep, rest = uri.partition("://")
    if not sep or scheme.lower() != GEONODE_SCHEME:
        raise ValueError(f"not a geonode URI: {uri!r}")
    inst, slash, dataset = rest.partition("/")
    if not inst or not slash or not dataset:
        raise ValueError(
            f"geonode URI must be 'geonode://<instance>/<dataset>', got {uri!r}"
        )
    return inst, dataset


def read_access(uri: str, *, registry: "GeoNodeRegistry | None" = None) -> AccessSpec:
    """Resolve a ``geonode://`` read URI to an :class:`AccessSpec`."""
    inst, dataset = parse_geonode_uri(uri)
    reg = registry if registry is not None else GEONODES
    return reg.resolve(inst).read_access(dataset)


# ---------------------------------------------------------------------------
# Write — GeoNode REST API v2 upload
# ---------------------------------------------------------------------------


def publish(
    gdf: Any,
    target: str,
    *,
    auth: str | None = None,
    overwrite: bool = True,
    registry: "GeoNodeRegistry | None" = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Publish a GeoDataFrame to GeoNode as a dataset (REST API v2 uploads).

    The frame is packaged as a GeoPackage and POSTed to the instance's
    uploads endpoint. The dataset name is the ``<dataset>`` part of
    *target* (``geonode://<instance>/<dataset>``).

    Args:
        gdf:       The GeoDataFrame to publish.
        target:    ``geonode://<instance>/<dataset>`` destination URI.
        auth:      Bearer token; falls back to the instance's declared auth.
        overwrite: Replace an existing dataset of the same name.
        registry:  GeoNode registry. Defaults to the process-wide one.
        timeout:   HTTP timeout in seconds.

    Returns:
        The parsed JSON upload response (or ``{"status": ...}`` on a
        non-JSON body).

    Raises:
        ValueError: on a malformed target.
        httpx.HTTPStatusError: on a non-2xx response.
    """
    import tempfile
    from pathlib import Path

    import httpx

    inst, dataset = parse_geonode_uri(target)
    reg = registry if registry is not None else GEONODES
    node = reg.resolve(inst)
    token = auth or node.auth

    with tempfile.TemporaryDirectory() as tmp:
        gpkg = Path(tmp) / f"{dataset}.gpkg"
        gdf.to_file(gpkg, layer=dataset, driver="GPKG")

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        data = {"dataset_title": dataset, "overwrite_existing_layer": str(overwrite).lower()}
        with gpkg.open("rb") as fh:
            files = {"base_file": (gpkg.name, fh, "application/geopackage+sqlite3")}
            resp = httpx.post(
                node.upload_endpoint(),
                headers=headers,
                data=data,
                files=files,
                timeout=timeout,
            )
    resp.raise_for_status()
    log.info("geonode_published", target=target, status=resp.status_code)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body is still a success
        return {"status": resp.status_code, "text": resp.text[:500]}


#: Process-wide GeoNode registry consulted by the unified loader.
GEONODES = GeoNodeRegistry()


__all__ = [
    "GEONODES",
    "GEONODE_ENV",
    "GEONODE_SCHEME",
    "GeoNode",
    "GeoNodeRegistry",
    "parse_geonode_uri",
    "publish",
    "read_access",
]
