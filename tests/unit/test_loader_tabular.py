"""Tests TDD — loader universel tabulaire non-géo (feat/loader-tabular).

Cas couverts :
- parquet géo → GeoDataFrame inchangé (régression)
- parquet non-géo AUTO → pd.DataFrame avec les bonnes données
- parquet non-géo geometry=True → ValueError claire
- parquet non-géo geometry=False → pd.DataFrame
- CSV non-géo (cp1252 !) avec cascade d'encodage → pd.DataFrame
- CSV avec lat/lon → reste GeoDataFrame en AUTO
- parquet corrompu (tronqué) → propagation de l'erreur, pas DataFrame vide
- manifest e2e source non-géo → le run ne lève pas
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from gispulse.persistence.loader import load


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def points_gdf() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"code_insee": ["75056", "69123"], "valeur": [1.0, 2.0]},
        geometry=[Point(2.35, 48.85), Point(4.83, 45.76)],
        crs="EPSG:4326",
    )


@pytest.fixture
def tabular_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"code_insee": ["75056", "69123", "13055"], "dvf_transactions": [100, 200, 150]}
    )


@pytest.fixture
def geo_parquet(tmp_path: Path, points_gdf: gpd.GeoDataFrame) -> Path:
    p = tmp_path / "geo.parquet"
    points_gdf.to_parquet(p)
    return p


@pytest.fixture
def tabular_parquet(tmp_path: Path, tabular_df: pd.DataFrame) -> Path:
    p = tmp_path / "tabular.parquet"
    tabular_df.to_parquet(p, index=False)
    return p


@pytest.fixture
def csv_latlon(tmp_path: Path) -> Path:
    """CSV UTF-8 avec colonnes lat/lon."""
    p = tmp_path / "pts.csv"
    p.write_text("code_insee,lat,lon\n75056,48.85,2.35\n69123,45.76,4.83\n", encoding="utf-8")
    return p


@pytest.fixture
def csv_no_geo(tmp_path: Path) -> Path:
    """CSV UTF-8 sans aucune colonne géométrique."""
    p = tmp_path / "nogeo.csv"
    p.write_text("code_insee,valeur\n75056,100\n69123,200\n13055,150\n", encoding="utf-8")
    return p


@pytest.fixture
def csv_cp1252_no_geo(tmp_path: Path) -> Path:
    """CSV encodé cp1252 sans colonne géo — simule les exports gouvernementaux FR."""
    p = tmp_path / "nogeo_cp1252.csv"
    # 'café' et 'résumé' encodés en cp1252
    content = "code_insee,libelle\n75056,caf\xe9\n69123,r\xe9sum\xe9\n"
    p.write_bytes(content.encode("cp1252"))
    return p


@pytest.fixture
def corrupted_parquet(tmp_path: Path) -> Path:
    """Fichier .parquet tronqué/corrompu."""
    p = tmp_path / "corrupt.parquet"
    # Magic bytes parquet mais contenu invalide
    p.write_bytes(b"PAR1" + b"\x00" * 20 + b"PAR1")
    return p


# ---------------------------------------------------------------------------
# Régression géo : comportement actuel inchangé
# ---------------------------------------------------------------------------


def test_load_geo_parquet_returns_geodataframe(geo_parquet: Path) -> None:
    result = load(str(geo_parquet))
    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 2
    assert result.crs is not None


def test_load_geo_parquet_geometry_true_returns_geodataframe(geo_parquet: Path) -> None:
    result = load(str(geo_parquet), geometry=True)
    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# AUTO (geometry=None) sur parquet non-géo
# ---------------------------------------------------------------------------


def test_load_tabular_parquet_auto_returns_dataframe(tabular_parquet: Path) -> None:
    result = load(str(tabular_parquet), geometry=None)
    assert isinstance(result, pd.DataFrame)
    assert not isinstance(result, gpd.GeoDataFrame)
    assert list(result.columns) == ["code_insee", "dvf_transactions"]
    assert len(result) == 3


def test_load_tabular_parquet_auto_preserves_values(tabular_parquet: Path, tabular_df: pd.DataFrame) -> None:
    result = load(str(tabular_parquet), geometry=None)
    assert list(result["code_insee"]) == list(tabular_df["code_insee"])
    assert list(result["dvf_transactions"]) == list(tabular_df["dvf_transactions"])


# ---------------------------------------------------------------------------
# geometry=True strict — erreur explicite sur parquet non-géo
# ---------------------------------------------------------------------------


def test_load_tabular_parquet_geometry_true_raises(tabular_parquet: Path) -> None:
    with pytest.raises((ValueError, Exception)) as exc_info:
        load(str(tabular_parquet), geometry=True)
    # Le message doit mentionner l'absence de géométrie, pas une corruption
    msg = str(exc_info.value).lower()
    assert any(kw in msg for kw in ("geo", "geometry", "spatial", "metadata")), (
        f"Expected geometry-related error, got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# geometry=False tabulaire direct
# ---------------------------------------------------------------------------


def test_load_tabular_parquet_geometry_false_returns_dataframe(tabular_parquet: Path) -> None:
    result = load(str(tabular_parquet), geometry=False)
    assert isinstance(result, pd.DataFrame)
    assert not isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 3


def test_load_geo_parquet_geometry_false_returns_plain_dataframe(geo_parquet: Path) -> None:
    """geometry=False force le retour pd.DataFrame même pour un GeoParquet."""
    result = load(str(geo_parquet), geometry=False)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# CSV non-géo — cascade d'encodage
# ---------------------------------------------------------------------------


def test_load_csv_no_geo_auto_returns_dataframe(csv_no_geo: Path) -> None:
    result = load(str(csv_no_geo), geometry=None)
    assert isinstance(result, pd.DataFrame)
    assert not isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 3
    assert "code_insee" in result.columns
    assert "valeur" in result.columns


def test_load_csv_cp1252_no_geo_cascade(csv_cp1252_no_geo: Path) -> None:
    """cp1252 : la cascade d'encodage doit produire un DataFrame sans UnicodeDecodeError."""
    result = load(str(csv_cp1252_no_geo), geometry=None)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2
    # cp1252 → 'é' doit être correctement décodé
    assert result["libelle"].iloc[0] == "café"
    assert result["libelle"].iloc[1] == "résumé"


def test_load_csv_no_geo_geometry_false(csv_no_geo: Path) -> None:
    result = load(str(csv_no_geo), geometry=False)
    assert isinstance(result, pd.DataFrame)
    assert not isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# CSV avec lat/lon — reste GeoDataFrame en AUTO
# ---------------------------------------------------------------------------


def test_load_csv_latlon_auto_returns_geodataframe(csv_latlon: Path) -> None:
    result = load(str(csv_latlon))
    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 2
    assert result.crs is not None


def test_load_csv_latlon_geometry_false_returns_dataframe(csv_latlon: Path) -> None:
    """geometry=False sur un CSV avec lat/lon : retour pd.DataFrame sans géométrie."""
    result = load(str(csv_latlon), geometry=False)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Parquet corrompu — erreur propagée, pas DataFrame vide
# ---------------------------------------------------------------------------


def test_load_corrupted_parquet_raises_not_returns_empty(corrupted_parquet: Path) -> None:
    """Un fichier parquet corrompu doit lever, pas retourner silencieusement un DataFrame vide."""
    with pytest.raises(Exception) as exc_info:
        load(str(corrupted_parquet))
    # On attend une vraie erreur de lecture, pas juste un DataFrame vide
    assert exc_info.value is not None
    # Ne doit PAS être une ValueError vague "no geo metadata" non plus —
    # l'erreur doit refléter la corruption (ArrowInvalid, OSError, etc.)
    # On vérifie juste qu'on lève bien quelque chose.
    err_type_name = type(exc_info.value).__name__
    assert err_type_name not in ("AssertionError",), f"Unexpected: {err_type_name}"


# ---------------------------------------------------------------------------
# Manifest e2e — source non-géo
# ---------------------------------------------------------------------------


def test_manifest_e2e_tabular_source(tmp_path: Path, tabular_df: pd.DataFrame) -> None:
    """run_manifest avec une source parquet non-géo ne doit pas lever."""
    from gispulse.core.manifest_v3 import (
        ManifestV3,
        ModelSpec,
        SourceSpec,
        validate_manifest,
    )
    from gispulse.runtime.manifest_runner import run_manifest

    # Écriture du parquet tabulaire
    parquet_path = tmp_path / "dvf.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    manifest = ManifestV3(
        name="test_tabular",
        sources={
            "dvf": SourceSpec(name="dvf", uri=str(parquet_path)),
        },
        models={
            "dvf_clean": ModelSpec(
                name="dvf_clean",
                select="dvf",
                transform=[],
                materialize="view",
            ),
        },
    )
    validate_manifest(manifest)

    loaded: dict = {}

    def source_loader(src):
        loaded[src.name] = True
        return load(src.uri, geometry=None)

    result = run_manifest(manifest, source_loader=source_loader)
    # Le manifest doit s'exécuter sans lever
    assert "dvf_clean" in result.materialized
    assert "dvf" in loaded
    # Le résultat est un DataFrame (pas forcément géo)
    assert isinstance(result.materialized["dvf_clean"].result, pd.DataFrame)
    assert len(result.materialized["dvf_clean"].result) == 3


# ---------------------------------------------------------------------------
# P1a — transforms tabulaires avec steps non vides → échec explicite
# ---------------------------------------------------------------------------


def test_manifest_tabular_source_with_transform_steps_raises(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """Source tabulaire + transform non vide → ValueError avec message actionnable."""
    from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec, validate_manifest
    from gispulse.runtime.manifest_runner import run_manifest

    parquet_path = tmp_path / "dvf.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    manifest = ManifestV3(
        name="test_tabular_with_steps",
        sources={"dvf": SourceSpec(name="dvf", uri=str(parquet_path))},
        models={
            "dvf_filtered": ModelSpec(
                name="dvf_filtered",
                select="dvf",
                transform=[{"filter": {"expression": "dvf_transactions > 1"}}],
                materialize="view",
            ),
        },
    )
    validate_manifest(manifest)

    def source_loader(src):
        return load(src.uri, geometry=None)

    with pytest.raises(ValueError) as exc_info:
        run_manifest(manifest, source_loader=source_loader)

    msg = str(exc_info.value).lower()
    # Le message doit être actionnable : nommer le modèle, mentionner tabulaire/transform
    assert "dvf_filtered" in msg
    assert any(kw in msg for kw in ("tabular", "transform", "geometry", "geodataframe"))


def test_manifest_tabular_source_no_steps_does_not_raise(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """Source tabulaire sans transform → pas de levée (régression P1a)."""
    from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec, validate_manifest
    from gispulse.runtime.manifest_runner import run_manifest

    parquet_path = tmp_path / "dvf.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    manifest = ManifestV3(
        name="test_tabular_passthrough",
        sources={"dvf": SourceSpec(name="dvf", uri=str(parquet_path))},
        models={
            "dvf_clean": ModelSpec(
                name="dvf_clean",
                select="dvf",
                transform=[],
                materialize="view",
            ),
        },
    )
    validate_manifest(manifest)

    result = run_manifest(
        manifest,
        source_loader=lambda src: load(src.uri, geometry=None),
    )
    assert "dvf_clean" in result.materialized
    assert len(result.materialized["dvf_clean"].result) == 3


# ---------------------------------------------------------------------------
# P1b — materialize: table avec source tabulaire → DuckDB register direct
# ---------------------------------------------------------------------------


def test_manifest_tabular_materialize_table_registers_in_duckdb(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """Source tabulaire + materialize=table → DataFrame enregistré dans DuckDB, interrogeable."""
    from gispulse.core.manifest_v3 import ManifestV3, ModelSpec, SourceSpec, validate_manifest
    from gispulse.runtime.manifest_runner import Materializer, run_manifest

    parquet_path = tmp_path / "dvf.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    manifest = ManifestV3(
        name="test_tabular_table",
        sources={"dvf": SourceSpec(name="dvf", uri=str(parquet_path))},
        models={
            "dvf_mart": ModelSpec(
                name="dvf_mart",
                select="dvf",
                transform=[],
                materialize="table",
            ),
        },
    )
    validate_manifest(manifest)

    # Moteur stub minimaliste : implémente register(name, df)
    registered: dict = {}

    class _StubEngine:
        def register(self, name: str, df: pd.DataFrame) -> None:
            registered[name] = df

    engine = _StubEngine()
    mat = Materializer(engine=engine)

    result = run_manifest(
        manifest,
        source_loader=lambda src: load(src.uri, geometry=None),
        materializer=mat,
    )

    assert "dvf_mart" in result.materialized
    # TABLE mode : table_ref renseigné
    assert result.materialized["dvf_mart"].table_ref is not None
    table_ref = result.materialized["dvf_mart"].table_ref
    # L'engine stub a bien reçu le DataFrame
    assert table_ref in registered
    assert len(registered[table_ref]) == 3
    assert list(registered[table_ref]["code_insee"]) == ["75056", "69123", "13055"]


# ---------------------------------------------------------------------------
# P2a — SourceResult tabulaire : geometry propagé à _result_to_gdf
# ---------------------------------------------------------------------------


def test_load_sourceresult_tabular_path_geometry_none_returns_dataframe(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """SourceResult data=path vers un parquet non-géo, geometry=None → pd.DataFrame."""
    from gispulse.core.plugin_model import Payload, SourceResult

    parquet_path = tmp_path / "tab.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    result_obj = SourceResult(payload=Payload.VECTOR, data=str(parquet_path))
    out = load(result_obj, geometry=None)
    assert isinstance(out, pd.DataFrame)
    assert not isinstance(out, gpd.GeoDataFrame)
    assert len(out) == 3


def test_load_sourceresult_tabular_path_geometry_true_raises(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """SourceResult data=path vers un parquet non-géo, geometry=True → ValueError."""
    from gispulse.core.plugin_model import Payload, SourceResult

    parquet_path = tmp_path / "tab.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    result_obj = SourceResult(payload=Payload.VECTOR, data=str(parquet_path))
    with pytest.raises((ValueError, Exception)) as exc_info:
        load(result_obj, geometry=True)
    msg = str(exc_info.value).lower()
    assert any(kw in msg for kw in ("geo", "geometry", "metadata"))


# ---------------------------------------------------------------------------
# P2b — contrat public : défaut geometry=True (strict-géo)
# ---------------------------------------------------------------------------


def test_load_default_geometry_is_strict_geo(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """Par défaut (geometry implicite), un parquet non-géo doit lever — pas retourner DataFrame."""
    parquet_path = tmp_path / "tab.parquet"
    tabular_df.to_parquet(parquet_path, index=False)
    # Appel sans geometry= : doit se comporter comme geometry=True (strict)
    with pytest.raises((ValueError, Exception)) as exc_info:
        load(str(parquet_path))
    msg = str(exc_info.value).lower()
    assert any(kw in msg for kw in ("geo", "geometry", "metadata"))


def test_load_csv_no_geo_default_raises(tmp_path: Path) -> None:
    """CSV sans colonnes géo, appel sans geometry= → ValueError (strict-géo par défaut)."""
    p = tmp_path / "nogeo.csv"
    p.write_text("code_insee,valeur\n75056,100\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc_info:
        load(str(p))
    assert "detect geometry" in str(exc_info.value).lower() or "geometry" in str(exc_info.value).lower()


def test_load_geo_parquet_default_returns_geodataframe(geo_parquet: Path) -> None:
    """GeoParquet sans geometry= → GeoDataFrame (comportement historique inchangé)."""
    result = load(str(geo_parquet))
    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# P3a — détection structurelle pyarrow (remplace le match textuel)
# ---------------------------------------------------------------------------


def test_load_tabular_parquet_auto_structural_detection(
    tmp_path: Path, tabular_df: pd.DataFrame
) -> None:
    """geometry=None détecte l'absence de méta-géo via pyarrow, pas via str du message."""
    parquet_path = tmp_path / "structural.parquet"
    tabular_df.to_parquet(parquet_path, index=False)

    result = load(str(parquet_path), geometry=None)
    assert isinstance(result, pd.DataFrame)
    assert not isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 3


def test_load_corrupted_parquet_geometry_none_raises(tmp_path: Path) -> None:
    """Parquet corrompu + geometry=None → l'erreur pyarrow se propage (pas de DataFrame vide)."""
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"PAR1" + b"\x00" * 20 + b"PAR1")

    with pytest.raises(Exception) as exc_info:
        load(str(corrupt), geometry=None)
    # Doit être une vraie erreur de lecture, pas AssertionError
    assert type(exc_info.value).__name__ != "AssertionError"


# ---------------------------------------------------------------------------
# P3b — collision sep dans geometry=False CSV
# ---------------------------------------------------------------------------


def test_load_csv_geometry_false_custom_sep(tmp_path: Path) -> None:
    """load(csv, geometry=False, sep=';') ne doit pas lever TypeError 'multiple values'."""
    p = tmp_path / "semi.csv"
    p.write_text("code_insee;valeur\n75056;100\n69123;200\n", encoding="utf-8")

    result = load(str(p), geometry=False, sep=";")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2
    assert list(result.columns) == ["code_insee", "valeur"]


def test_load_csv_geometry_none_custom_sep_no_geo(tmp_path: Path) -> None:
    """load(csv, geometry=None, sep=';') fallback tabulaire avec séparateur custom."""
    p = tmp_path / "semi.csv"
    p.write_text("code_insee;valeur\n75056;100\n69123;200\n", encoding="utf-8")

    result = load(str(p), geometry=None, sep=";")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2
