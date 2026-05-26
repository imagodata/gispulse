# INSEE IRIS Sociodemographics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the reusable INSEE source plugin with IRIS sociodemographic tables, then let Foncier Radar consume IRIS as the named neighborhood aggregate level.

**Architecture:** Keep source acquisition in `gispulse-src-insee`; do not ingest raw INSEE CSVs directly inside `gispulse-foncier`. IRIS contours and IRIS socio-demo belong to the same semantic INSEE source, with one access protocol per entry. Foncier Radar consumes plugin-backed outputs, spatially assigns parcels to IRIS, and exposes IRIS between commune and cadastral section in the map pyramid.

**Tech Stack:** Python, GISPulse source plugins, `AccessSpec`, `AccessProtocol`, pytest, dbt/DuckDB, PostGIS, Martin MVT, Astro/React MapLibre frontend.

---

## Branching Decision

Do **not** build this on the Garage branch.

Observed on 2026-05-25:

- `beta/garage-config-s3-env` lives in `/private/tmp/gispulse-garage-config-beta` and carries Garage/S3 materialization work.
- `feat/garage-object-store` is also storage/infrastructure work.
- The IRIS socio-demo work is source/catalog/product semantics, not object-store plumbing.
- The old `feat/src-insee` branch is stale relative to `main`; the INSEE contour work already landed on `main` through a squash/merge path.

Recommended stack:

1. `gispulse/gispulse`: branch from current `main`, first PR for `gispulse-src-insee` socio-demo entries.
2. `gispulse-foncier`: branch after the core plugin contract is merged or pinned.
3. `foncier-radar-app`: branch after the backend serving contract exposes `aggregate_level = 'iris'`.

## Source Decisions

The plugin should model reusable INSEE datasets, not a Foncier-specific denormalized table.

Current official source candidates verified on 2026-05-25:

- Population 2022, base infracommunale IRIS: https://www.insee.fr/fr/statistiques/8647014
- Logement 2022, base infracommunale IRIS: https://www.insee.fr/fr/statistiques/8647012
- Couples, familles, ménages 2022, base infracommunale IRIS: https://www.insee.fr/fr/statistiques/8647008
- Activité des résidents 2022, base infracommunale IRIS: https://www.insee.fr/fr/statistiques/8647006
- Diplômes, formation 2022, base infracommunale IRIS: https://www.insee.fr/fr/statistiques/8647010
- Revenus, pauvreté et niveau de vie 2021, Filosofi IRIS: https://www.insee.fr/fr/statistiques/8229323

Important semantics:

- IRIS is the product "quartier" level for Foncier Radar.
- IRIS is not a cadastral subdivision and must not be derived by rolling up cadastral sections.
- Recensement IRIS 2022 uses geography in force on 2024-01-01.
- Filosofi IRIS 2021 uses geography in force on 2022-01-01 and has confidentiality/non-summability constraints.
- Small communes not split into IRIS are represented at commune level in the INSEE IRIS files to preserve coverage.
- The recensement IRIS CSV data files carry raw geographic keys such as `IRIS`,
  `COM`, `TYP_IRIS`, `LAB_IRIS`; human labels such as `nom_iris` and `nom_com`
  should come from the IRIS contour entry.

## Aggregate Order

Map pyramid:

1. National
2. Department
3. Commune
4. IRIS
5. Cadastral section
6. Parcel

Recommended zoom policy for Foncier Radar:

- `z0`: national
- `z1-z6`: department
- `z7-z11`: commune
- `z12`: IRIS
- `z13`: section
- `z14+`: parcel

Score fallback:

1. Parcel
2. Section
3. IRIS
4. Commune

Reason: section is the tighter cadastral signal already used by the scoring model; IRIS is the human-readable neighborhood and socio-demo benchmark. Add an explicit `score_opportunity_relative_iris` for neighborhood comparison instead of silently replacing section behavior.

## Task 1: Source Contract Audit

**Files:**

- Modify: `plugins/gispulse-src-insee/README.md`
- Modify: `plugins/gispulse-src-insee/gispulse_src_insee/source.py`
- Test: `tests/unit/test_src_insee.py`

- [ ] **Step 1: Record source catalog candidates in README**

Add a "Sociodemographic IRIS sources" section to `plugins/gispulse-src-insee/README.md` with the six source URLs above, the millesimes, and the geography dates.

- [ ] **Step 2: Add test coverage for current contour entry before changing behavior**

In `tests/unit/test_src_insee.py`, keep or add assertions that the existing `iris` entry remains:

```python
def test_insee_iris_contour_entry_stays_wfs() -> None:
    from gispulse.core.plugin_model import AccessProtocol, Payload
    from gispulse_src_insee.source import InseeSource

    source = InseeSource()
    entry = next(e for e in source.entries() if e.id == "iris")

    assert entry.payload is Payload.VECTOR
    assert entry.access.protocol is AccessProtocol.WFS
    assert entry.access.params["typename"] == "STATISTICALUNITS.IRIS:contour_iris"
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
uv run pytest tests/unit/test_src_insee.py -q
```

Expected: pass before and after the socio-demo entries are added.

## Task 2: Add INSEE Socio-Demo Entries

**Files:**

- Modify: `plugins/gispulse-src-insee/gispulse_src_insee/source.py`
- Modify: `plugins/gispulse-src-insee/README.md`
- Test: `tests/unit/test_src_insee.py`

- [ ] **Step 1: Add failing tests for table entries**

Add assertions for entries named:

- `iris_population_2022`
- `iris_logement_2022`
- `iris_menages_2022`
- `iris_activite_2022`
- `iris_diplomes_2022`
- `iris_filosofi_revenus_declares_2021`
- `iris_filosofi_revenus_disponibles_2021`

Expected contract:

```python
def test_insee_iris_sociodemo_entries_are_tables() -> None:
    from gispulse.core.plugin_model import Payload
    from gispulse_src_insee.source import InseeSource

    source = InseeSource()
    by_id = {entry.id: entry for entry in source.entries()}

    for entry_id in {
        "iris_population_2022",
        "iris_logement_2022",
        "iris_menages_2022",
        "iris_activite_2022",
        "iris_diplomes_2022",
        "iris_filosofi_revenus_declares_2021",
        "iris_filosofi_revenus_disponibles_2021",
    }:
        assert by_id[entry_id].payload is Payload.TABLE
        assert by_id[entry_id].domain.value == "statistique"
        assert by_id[entry_id].jurisdiction == "FR"
```

- [ ] **Step 2: Add entries to `InseeSource.entries()`**

Use one entry per source file/theme. Keep `iris` as the WFS contour entry for backward compatibility.

Transport rule:

- If the final URL is a stable CSV/ZIP file URL, use `AccessProtocol.TABLE_FILE`.
- The existing `DOWNLOAD` path is vector-oriented and should not be reused for non-spatial INSEE tables.
- If INSEE exposes a JSON API for the same files with pagination, use `REST_TABLE`.

- [ ] **Step 3: Make `schema()` entry-aware**

The contour schema must keep:

- `code_iris`
- `nom_iris`
- `insee_com`
- `nom_com`
- `type_iris`
- `geometry`

The recensement table schemas must expose at least:

- `IRIS`
- `COM`
- `TYP_IRIS`
- `LAB_IRIS`
- dataset-specific raw variables

Filosofi table schemas expose `IRIS` plus dataset-specific raw variables.

Do not normalize product-specific aliases like `revenue_median` in the plugin. That belongs in consumer models such as `gispulse-foncier`.

- [ ] **Step 4: Make `revision()` entry-aware**

The contour revision can keep the current WFS behavior. Table revisions must return a stable millesime/file identity such as:

- `insee-rp-iris-population-2022-geo-2024-01-01`
- `insee-filosofi-iris-revenus-2021-geo-2022-01-01`

- [ ] **Step 5: Run plugin tests**

Run:

```bash
uv run pytest tests/unit/test_src_insee.py tests/unit/test_plugin_model.py -q
```

Expected: all selected tests pass.

## Task 3: Add Or Fix Table File Fetching If Needed

**Files:**

- Modify or create: `src/gispulse/core/fetchers/table_file.py`
- Modify: `src/gispulse/core/fetchers/__init__.py`
- Modify: `src/gispulse/core/plugin_model.py`
- Test: `tests/unit/test_table_file_fetcher.py`
- Test: `tests/unit/test_src_insee.py`

- [ ] **Step 1: Verify the current fetcher contract**

Run the current INSEE entry test suite and inspect `AccessProtocol.DOWNLOAD` behavior. If CSV download requires latitude/longitude or geometry, do not use it for non-spatial INSEE tables.

- [ ] **Step 2: Add table-file fetcher test**

Create `tests/unit/test_table_file_fetcher.py` with a local CSV fixture and assert:

```python
def test_table_file_fetcher_materializes_csv_as_table(tmp_path: Path) -> None:
    from gispulse.core.fetchers.table_file import TableFileFetcher
    from gispulse.core.plugin_model import AccessProtocol, AccessSpec, FetchMode, Payload

    csv_path = tmp_path / "iris.csv"
    csv_path.write_text("IRIS;COM\n631130101;63113\n", encoding="utf-8")

    result = TableFileFetcher().fetch(
        AccessSpec(protocol=AccessProtocol.TABLE_FILE, endpoint=str(csv_path)),
        mode=FetchMode.MATERIALIZE,
    )

    assert result.payload is Payload.TABLE
    assert result.data == str(csv_path)
```

- [ ] **Step 3: Implement the minimal fetcher**

The fetcher must:

- return `Payload.TABLE`;
- support materialized local file output;
- not invent geometries;
- preserve raw CSV/XLSX files or normalized CSV/Parquet files according to existing core conventions.

- [ ] **Step 4: Register the fetcher**

Register the table-file protocol through the same dispatch mechanism used by the existing core fetchers.

- [ ] **Step 5: Run fetcher and source tests**

Run:

```bash
uv run pytest tests/unit/test_table_file_fetcher.py tests/unit/test_src_insee.py -q
```

Expected: all selected tests pass.

## Task 4: Publish Core Plugin Contract

**Files:**

- Modify: `plugins/gispulse-src-insee/README.md`
- Modify: `docs-site/plugins/src-insee.md` if this docs page exists when implementing
- Modify: `CHANGELOG.md` only if the repo release process requires it

- [ ] **Step 1: Document entry IDs and payloads**

Document every entry with:

- entry id;
- payload;
- protocol;
- source URL;
- millesime;
- geography date;
- known confidentiality caveat.

- [ ] **Step 2: Run focused quality gate**

Run:

```bash
uv run pytest tests/unit/test_src_insee.py -q
uv run ruff check plugins/gispulse-src-insee tests/unit/test_src_insee.py
```

Expected: pass.

- [ ] **Step 3: Review branch before merge**

Run:

```bash
git diff --stat main...HEAD
git diff --check
```

Expected: only INSEE plugin/docs/tests/fetcher files changed, no Garage/S3 files changed.

## Task 5: Foncier Backend Integration

**Repository:** `gispulse-foncier`

**Files:**

- Create: `scripts/ingest_iris_dept.py`
- Create: `scripts/ingest_iris_socio.py`
- Modify: `pyproject.toml`
- Modify: `dbt/models/staging/sources.yml`
- Create: `dbt/models/staging/stg_iris.sql`
- Create: `dbt/models/staging/stg_iris_socio.sql`
- Create: `dbt/models/intermediate/int_parcel_iris.sql`
- Create: `dbt/models/intermediate/int_iris_growth.sql`
- Create: `dbt/models/intermediate/int_iris_stats.sql`
- Modify: `dbt/models/intermediate/int_score_opportunity_smoothed.sql`
- Create: `dbt/models/intermediate/int_cells_iris.sql`
- Modify: `dbt/models/marts/mart_radar_parcels.sql`
- Create: `dbt/models/marts/mart_cells_iris_pyramid.sql`
- Create: `dbt/models/serving/serving_radar_cells_iris.sql`
- Modify: `dbt/models/serving/serving_radar_cells_unified.sql`
- Modify: `dbt/models/serving/serving_radar_parcels.sql`
- Modify: `dbt/macros/atomic_promote.sql`
- Modify: `dbt/macros/ensure_serving_indexes.sql`

- [ ] **Step 1: Consume plugin-backed INSEE outputs**

`scripts/ingest_iris_dept.py` should fetch contours through `gispulse-src-insee` entry `iris`.

`scripts/ingest_iris_socio.py` should fetch socio-demo entries through the new INSEE plugin table entries. It must not hard-code raw INSEE CSV paths except as plugin configuration inputs.

- [ ] **Step 2: Add parcel to IRIS spatial join**

Use parcel centroid within IRIS polygon:

```sql
ST_Within(ST_Centroid(parcel.geom), iris.geom)
```

Output:

- `id_parcelle`
- `code_iris`
- `nom_iris`
- `code_insee`

- [ ] **Step 3: Insert IRIS into score fallback**

Update fallback from:

```sql
coalesce(parcel_growth, section_growth, commune_growth)
```

to:

```sql
coalesce(parcel_growth, section_growth, iris_growth, commune_growth)
```

Add a separate `score_opportunity_relative_iris` metric for neighborhood-relative comparison.

- [ ] **Step 4: Add IRIS serving cell**

`serving_radar_cells_iris.sql` should expose the standard cell contract plus:

- `code_iris`
- `nom_iris`
- selected headline socio-demo fields

Keep detailed socio-demo available for cell detail payloads rather than pushing every raw INSEE variable into MVT tiles.

- [ ] **Step 5: Run backend verification**

Run the repo's focused dbt/pytest commands from its runbook. Minimum expected checks:

```bash
uv run pytest tests/test_radar_promotion.py tests/test_radar_postgis.py -q
```

Expected: pass, then add focused dbt build once fixtures include IRIS rows.

## Task 6: Foncier Zoom And MVT Contract

**Repository:** `gispulse-foncier`

**Files:**

- Modify: `src/gispulse_foncier/radar_contracts.py`
- Modify: `martin/config.yaml`
- Modify: `martin/migrations/01_radar_cells_functions.sql`
- Modify: `dbt/models/serving/serving_radar_cells_unified.sql`

- [ ] **Step 1: Update zoom constants**

Use:

- national: `0`
- department: `1-6`
- commune: `7-11`
- IRIS: `12`
- section: `13`
- parcel: `14+`

- [ ] **Step 2: Add Martin function**

Create `radar_cells_iris_mvt(z, x, y)` reading `main_public.radar_cells_iris_active`.

The MVT layer name must remain `radar_cells` so the frontend's shared cell layer keeps rendering.

- [ ] **Step 3: Keep MVT payload thin**

Include:

- `cell_id`
- `aggregate_level`
- `code_insee`
- `commune`
- `code_iris`
- `nom_iris`
- scores/counts/coverage fields

Do not include the full raw socio-demo column set in vector tiles.

## Task 7: Frontend Contract

**Repository:** `foncier-radar-app`

**Files:**

- Modify: `src/pages/api/radar/tiles/[z]/[x]/[y].mvt.ts`
- Modify: `src/islands/RadarMap/index.tsx`
- Modify: `src/islands/RadarMap/aggregate-selection.ts`
- Modify: `src/lib/types.ts`
- Modify: `src/lib/radar-contract.ts`
- Modify: `src/components/ParcelDetailPanel.tsx`
- Modify: `tests/radar-proxy.test.ts`
- Modify: `tests/radar-aggregate-selection.test.ts`

- [ ] **Step 1: Add `iris` aggregate level**

Update `RadarAggregateLevel` and validators to include:

```ts
"iris"
```

- [ ] **Step 2: Update tile source ranges**

Add `radar_cells_iris` at z12 and move section to z13.

- [ ] **Step 3: Display IRIS as neighborhood**

For selected aggregate cells:

- title: `nom_iris`
- label: `Quartier IRIS`
- parent subtitle: commune when available

- [ ] **Step 4: Show parcel neighborhood**

Parcel detail should display:

```text
Quartier : {nom_iris}
```

- [ ] **Step 5: Run frontend checks**

Run:

```bash
pnpm run check
pnpm test -- tests/radar-proxy.test.ts tests/radar-aggregate-selection.test.ts
```

Expected: pass.

## Review Gates

Each PR should finish with:

```bash
git diff --check
git status --short
```

And the relevant focused test command.

Do not merge the Foncier backend PR until the core INSEE plugin PR is merged or pinned with an explicit dependency reference.

Do not merge the frontend PR until the backend serving contract exposes stable `aggregate_level = 'iris'`, `code_iris`, and `nom_iris` fields.
