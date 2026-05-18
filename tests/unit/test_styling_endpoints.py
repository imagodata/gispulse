"""Tests for v1.5 styling endpoints (breaks / PUT styles / import QML)."""

from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from gispulse.core.models import Dataset
from gispulse.adapters.http.app import create_app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GISPULSE_STORAGE", "memory")
    monkeypatch.delenv("GISPULSE_API_KEYS", raising=False)
    from gispulse.adapters.http.rate_limit import limiter
    limiter.enabled = False


@pytest.fixture()
def gpkg_path(tmp_path: Path) -> Path:
    """Build a tiny polygon GPKG with a numeric attribute for classification."""
    polys = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(20)]
    gdf = gpd.GeoDataFrame(
        {"pop": list(range(1, 21)), "category": ["A"] * 7 + ["B"] * 7 + ["C"] * 6},
        geometry=polys,
        crs="EPSG:4326",
    )
    p = tmp_path / "fixture.gpkg"
    gdf.to_file(p, layer="parcels", driver="GPKG")
    return p


@pytest.fixture()
def client(gpkg_path: Path) -> tuple[TestClient, str]:
    os.environ["GISPULSE_STORAGE"] = "memory"
    app = create_app()
    ds = Dataset(name="fixture", source_path=str(gpkg_path), crs="EPSG:4326", format="gpkg")
    app.state.dataset_repo.save(ds)
    return TestClient(app, raise_server_exceptions=False), str(ds.id)


# ── POST /datasets/{id}/layers/{layer}/breaks ────────────────────────────


class TestComputeBreaks:
    def test_jenks_returns_n_plus_one_breaks(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "pop", "method": "jenks", "n_classes": 5},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["field"] == "pop"
        assert body["method"] == "jenks"
        assert len(body["breaks"]) == 6
        assert len(body["labels"]) == 5
        assert body["breaks"][0] == 1.0
        assert body["breaks"][-1] == 20.0

    def test_quantile_method(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "pop", "method": "quantile", "n_classes": 4},
        )
        assert r.status_code == 200
        assert len(r.json()["breaks"]) == 5

    def test_invalid_method_returns_400(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "pop", "method": "kmeans", "n_classes": 5},
        )
        assert r.status_code == 400

    def test_non_numeric_field_returns_400(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "category", "method": "jenks", "n_classes": 3},
        )
        assert r.status_code == 400

    def test_unknown_field_returns_404(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "missing", "method": "jenks", "n_classes": 5},
        )
        assert r.status_code == 404

    def test_unknown_layer_returns_404(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/ghost/breaks",
            json={"field": "pop", "method": "jenks", "n_classes": 5},
        )
        assert r.status_code == 404

    def test_n_classes_out_of_range(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/layers/parcels/breaks",
            json={"field": "pop", "method": "jenks", "n_classes": 1},
        )
        assert r.status_code == 400


# ── PUT /datasets/{id}/styles ────────────────────────────────────────────


class TestPutStyle:
    def test_persists_style_def_as_qml(self, client, gpkg_path):
        c, ds_id = client
        style_def = {
            "renderer": "single",
            "symbol": {
                "kind": "fill",
                "color": "#ff0000",
                "opacity": 0.5,
                "strokeColor": "#000000",
                "strokeWidth": 1.0,
            },
        }
        r = c.put(
            f"/api/portal/datasets/{ds_id}/styles",
            json={"layer_name": "parcels", "style_def": style_def, "geom_type": "polygon"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["layer_name"] == "parcels"
        assert r.json()["qml_size_bytes"] > 0

        from gispulse.persistence.gpkg import read_styles
        rows = read_styles(str(gpkg_path))
        parcels_styles = [row for row in rows if row["f_table_name"] == "parcels"]
        assert len(parcels_styles) == 1
        assert "<qgis" in parcels_styles[0]["styleQML"]

    def test_replaces_existing_style(self, client, gpkg_path):
        c, ds_id = client
        sd1 = {"renderer": "single", "symbol": {"kind": "fill", "color": "#ff0000", "opacity": 0.5, "strokeColor": "#000", "strokeWidth": 1}}
        sd2 = {"renderer": "single", "symbol": {"kind": "fill", "color": "#00ff00", "opacity": 0.7, "strokeColor": "#000", "strokeWidth": 1}}
        c.put(f"/api/portal/datasets/{ds_id}/styles", json={"layer_name": "parcels", "style_def": sd1, "geom_type": "polygon"})
        c.put(f"/api/portal/datasets/{ds_id}/styles", json={"layer_name": "parcels", "style_def": sd2, "geom_type": "polygon"})
        from gispulse.persistence.gpkg import read_styles
        rows = [row for row in read_styles(str(gpkg_path)) if row["f_table_name"] == "parcels"]
        assert len(rows) == 1

    def test_invalid_style_def_returns_400(self, client):
        c, ds_id = client
        r = c.put(
            f"/api/portal/datasets/{ds_id}/styles",
            json={"layer_name": "parcels", "style_def": "not a dict", "geom_type": "polygon"},
        )
        assert r.status_code in (400, 422)

    def test_unknown_dataset_returns_404(self, client):
        c, _ = client
        r = c.put(
            "/api/portal/datasets/00000000-0000-0000-0000-000000000000/styles",
            json={"layer_name": "parcels", "style_def": {"renderer": "single"}, "geom_type": "polygon"},
        )
        assert r.status_code == 404


# ── POST /datasets/{id}/styles/import ────────────────────────────────────


_QML_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<qgis version="3.34">
  <renderer-v2 type="singleSymbol">
    <symbols>
      <symbol name="0" type="fill">
        <layer class="SimpleFill">
          <prop k="color" v="31,120,180,180" />
          <prop k="outline_color" v="0,0,0,255" />
          <prop k="outline_width" v="0.5" />
          <prop k="style" v="solid" />
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>"""


class TestImportQml:
    def test_imports_qml_and_returns_style_def(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/styles/import",
            data={"layer_name": "parcels", "geom_type": "polygon"},
            files={"file": ("fixture.qml", _QML_FIXTURE, "application/xml")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["layer_name"] == "parcels"
        assert body["style_def"]["renderer"] == "single"
        assert body["style_def"]["symbol"]["kind"] == "fill"

    def test_persists_imported_qml_to_gpkg(self, client, gpkg_path):
        c, ds_id = client
        c.post(
            f"/api/portal/datasets/{ds_id}/styles/import",
            data={"layer_name": "parcels", "geom_type": "polygon"},
            files={"file": ("fixture.qml", _QML_FIXTURE, "application/xml")},
        )
        from gispulse.persistence.gpkg import read_styles
        rows = [row for row in read_styles(str(gpkg_path)) if row["f_table_name"] == "parcels"]
        assert len(rows) == 1
        assert "<qgis" in rows[0]["styleQML"]

    def test_invalid_xml_returns_400(self, client):
        c, ds_id = client
        r = c.post(
            f"/api/portal/datasets/{ds_id}/styles/import",
            data={"layer_name": "parcels", "geom_type": "polygon"},
            files={"file": ("bad.qml", "<not xml at all", "application/xml")},
        )
        assert r.status_code == 400

    def test_oversized_file_returns_413(self, client):
        c, ds_id = client
        big = "<qgis>" + ("x" * 1_100_000) + "</qgis>"
        r = c.post(
            f"/api/portal/datasets/{ds_id}/styles/import",
            data={"layer_name": "parcels", "geom_type": "polygon"},
            files={"file": ("big.qml", big, "application/xml")},
        )
        assert r.status_code == 413

    def test_unknown_dataset_returns_404(self, client):
        c, _ = client
        r = c.post(
            "/api/portal/datasets/00000000-0000-0000-0000-000000000000/styles/import",
            data={"layer_name": "parcels", "geom_type": "polygon"},
            files={"file": ("fixture.qml", _QML_FIXTURE, "application/xml")},
        )
        assert r.status_code == 404
