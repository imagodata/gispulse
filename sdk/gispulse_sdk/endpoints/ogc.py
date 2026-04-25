"""OGC API Features endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class OGCEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    def landing(self) -> dict:
        return self._c._request("GET", "/ogc/")

    def conformance(self) -> dict:
        return self._c._request("GET", "/ogc/conformance")

    def collections(self) -> list[dict]:
        resp = self._c._request("GET", "/ogc/collections")
        return resp.get("collections", resp) if isinstance(resp, dict) else resp

    def collection(self, collection_id: str) -> dict:
        return self._c._request("GET", f"/ogc/collections/{collection_id}")

    def items(
        self,
        collection_id: str,
        limit: int = 100,
        offset: int = 0,
        bbox: tuple | None = None,
    ) -> dict:
        """Query features as GeoJSON FeatureCollection."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if bbox:
            params["bbox"] = ",".join(str(v) for v in bbox)
        return self._c._request(
            "GET", f"/ogc/collections/{collection_id}/items", params=params
        )

    def item(self, collection_id: str, feature_id: str) -> dict:
        return self._c._request(
            "GET", f"/ogc/collections/{collection_id}/items/{feature_id}"
        )

    def collection_url(self, collection_id: str) -> str:
        """Return the full OGC collection URL for use in GIS clients (QGIS, ArcGIS)."""
        return f"{self._c._base_url}/ogc/collections/{collection_id}"

    def items_url(self, collection_id: str) -> str:
        """Return the full OGC items URL for use as a WFS-like data source."""
        return f"{self._c._base_url}/ogc/collections/{collection_id}/items"
