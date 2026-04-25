"""Tests for the datasets endpoint."""

from __future__ import annotations


from gispulse_sdk.models import DatasetResponse


DATASET_JSON = {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "parcels",
    "source_path": "/data/parcels.gpkg",
    "data_category": "vector",
    "crs": "EPSG:4326",
    "format": "GPKG",
    "metadata": {},
    "created_at": "2026-01-01T00:00:00",
}


class TestDatasetsList:
    def test_list_returns_models(self, client, mock_api):
        mock_api.get("/datasets").respond(200, json=[DATASET_JSON])
        result = client.datasets.list()
        assert len(result) == 1
        assert isinstance(result[0], DatasetResponse)
        assert result[0].name == "parcels"

    def test_list_paginated_response(self, client, mock_api):
        mock_api.get("/datasets").respond(
            200,
            json={"items": [DATASET_JSON], "total": 1, "limit": 100, "offset": 0},
        )
        result = client.datasets.list()
        assert len(result) == 1


class TestDatasetsGet:
    def test_get_by_id(self, client, mock_api):
        mock_api.get("/datasets/3fa85f64-5717-4562-b3fc-2c963f66afa6").respond(
            200, json=DATASET_JSON
        )
        ds = client.datasets.get("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        assert ds.crs == "EPSG:4326"
        assert ds.data_category == "vector"
