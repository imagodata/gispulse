"""Dataset endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from gispulse_sdk.models import DatasetResponse, OGCDatasetCreate

if TYPE_CHECKING:
    from gispulse_sdk.client import GISPulseClient


class DatasetsEndpoint:
    def __init__(self, client: GISPulseClient):
        self._c = client

    # -- /datasets (core API) --

    def upload(
        self,
        file_path: str | Path,
        name: str | None = None,
    ) -> DatasetResponse:
        """Upload a spatial file and register as a dataset."""
        p = Path(file_path)
        fname = name or p.name
        with open(p, "rb") as f:
            resp = self._c._request(
                "POST",
                "/datasets/upload",
                files={"file": (fname, f)},
            )
        return DatasetResponse.model_validate(resp)

    def upload_ogc(self, payload: OGCDatasetCreate) -> DatasetResponse:
        """Register a remote OGC service as a dataset (lazy, no download)."""
        resp = self._c._request("POST", "/datasets/ogc", json=payload.model_dump())
        return DatasetResponse.model_validate(resp)

    def list(self, limit: int = 100, offset: int = 0) -> list[DatasetResponse]:
        """List registered datasets."""
        resp = self._c._request("GET", "/datasets", params={"limit": limit, "offset": offset})
        items = resp if isinstance(resp, list) else resp.get("items", resp)
        return [DatasetResponse.model_validate(d) for d in items]

    def get(self, dataset_id: UUID | str) -> DatasetResponse:
        """Get a single dataset by ID."""
        resp = self._c._request("GET", f"/datasets/{dataset_id}")
        return DatasetResponse.model_validate(resp)

    # -- /api/portal/datasets (portal API, richer features) --

    def portal_upload(
        self,
        file_path: str | Path,
        name: str | None = None,
        force: bool = False,
    ) -> dict:
        """Upload via the portal endpoint (supports duplicate detection)."""
        p = Path(file_path)
        fname = name or p.name
        with open(p, "rb") as f:
            resp = self._c._request(
                "POST",
                "/api/portal/datasets/upload",
                files={"file": (fname, f)},
                params={"force": str(force).lower()},
            )
        return resp

    def portal_list(self) -> list[dict]:
        """List datasets via the portal endpoint."""
        return self._c._request("GET", "/api/portal/datasets")

    def features(
        self,
        dataset_id: UUID | str,
        layer: str | None = None,
        limit: int = 100,
        offset: int = 0,
        bbox: tuple | None = None,
    ) -> dict:
        """Get features as GeoJSON FeatureCollection."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if bbox:
            params["bbox"] = ",".join(str(v) for v in bbox)
        layer_name = layer or "default"
        return self._c._request(
            "GET",
            f"/api/portal/datasets/{dataset_id}/layers/{layer_name}/features",
            params=params,
        )

    def sql(self, query: str) -> dict:
        """Execute a SQL query against loaded datasets."""
        return self._c._request("POST", "/api/portal/sql/execute", json={"query": query})

    def export(
        self,
        dataset_id: UUID | str,
        format: str = "gpkg",
        output_path: str | Path | None = None,
    ) -> Path:
        """Export dataset to a file. Returns path to the downloaded file."""
        resp = self._c._http.post(
            f"{self._c._base_url}/api/portal/datasets/export",
            json={"dataset_id": str(dataset_id), "format": format},
        )
        from gispulse_sdk.exceptions import raise_for_status
        raise_for_status(resp.status_code, resp.text)

        out = Path(output_path) if output_path else Path(f"export_{dataset_id}.{format}")
        out.write_bytes(resp.content)
        return out

    def delete(self, dataset_id: UUID | str) -> dict:
        """Delete a dataset."""
        return self._c._request("DELETE", f"/api/portal/datasets/{dataset_id}")
