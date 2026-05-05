"""Tests for ``gispulse.runtime.config_loader``.

Coverage targets:
- valid YAML round-trip through pydantic v2 strict schema
- ``yaml.load`` (unsafe) is never used by the module (static grep test)
- Path traversal escapes (``../../etc/passwd``) are rejected
- Schema typos surface clear errors
- ``validate_against_gpkg`` catches non-existent tables
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gispulse.runtime.config_loader import (
    ConfigError,
    GISPulseConfig,
    load_config,
    to_triggers,
    validate_against_gpkg,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracked_gpkg(tmp_path: Path) -> Path:
    """Create a real GPKG with a ``parcels`` table tracked by triggers."""
    from persistence.gpkg_engine import GeoPackageEngine

    gpkg = tmp_path / "fixture.gpkg"
    engine = GeoPackageEngine(path=gpkg)
    engine.open()
    try:
        conn = engine._get_conn()  # noqa: SLF001
        conn.execute(
            'CREATE TABLE "parcels" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)'
        )
        conn.execute(
            'CREATE TABLE "buildings" '
            '(fid INTEGER PRIMARY KEY AUTOINCREMENT, height REAL)'
        )
        conn.commit()
        engine.enable_change_tracking("parcels")
        engine.enable_change_tracking("buildings")
    finally:
        engine.close()
    return gpkg


@pytest.fixture()
def valid_yaml(tracked_gpkg: Path, tmp_path: Path) -> Path:
    cfg = tmp_path / "triggers.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: enrich
                table: parcels
                pk_col: fid
                when: [INSERT, UPDATE]
                actions:
                  - type: webhook
                    url: https://hook.example.com/parcels
                  - type: set_field
                    field: status
                    value: enriched
                  - type: run_sql
                    expression: "SELECT 1"
            security:
              webhook_allowlist:
                - hook.example.com
            runtime:
              poll_interval_ms: 500
              max_batch: 50
            """,
        ).strip(),
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_config_valid_yaml(valid_yaml: Path, tracked_gpkg: Path) -> None:
    cfg = load_config(valid_yaml)
    assert isinstance(cfg, GISPulseConfig)
    assert cfg.version == 1
    assert Path(cfg.gpkg) == tracked_gpkg.resolve()
    assert len(cfg.triggers) == 1
    t = cfg.triggers[0]
    assert t.name == "enrich"
    assert t.table == "parcels"
    assert t.when == ["INSERT", "UPDATE"]
    assert len(t.actions) == 3
    assert t.actions[0].type == "webhook"
    assert cfg.runtime.poll_interval_ms == 500
    assert cfg.runtime.max_batch == 50
    assert cfg.security.webhook_allowlist == ["hook.example.com"]


def test_to_triggers_maps_into_domain(valid_yaml: Path) -> None:
    from core.graph import ActionType

    cfg = load_config(valid_yaml)
    triggers = to_triggers(cfg)
    assert len(triggers) == 1
    trig = triggers[0]
    assert trig.name == "enrich"
    assert trig.enabled is True
    assert {a.action_type for a in trig.actions} == {
        ActionType.WEBHOOK,
        ActionType.SET_FIELD,
        ActionType.RUN_SQL,
    }
    # YAML metadata stashed on conditions for downstream introspection.
    assert trig.conditions["yaml_name"] == "enrich"
    assert trig.conditions["table"] == "parcels"
    assert trig.conditions["when"] == ["INSERT", "UPDATE"]


def test_validate_against_gpkg_passes_when_tables_exist(valid_yaml: Path) -> None:
    cfg = load_config(valid_yaml)
    errors = validate_against_gpkg(cfg)
    assert errors == []


def test_gpkg_override_wins_over_config_value(
    valid_yaml: Path, tracked_gpkg: Path
) -> None:
    # Build a second GPKG and pass it via override.
    other = tracked_gpkg.parent / "other.gpkg"
    from persistence.gpkg_engine import GeoPackageEngine

    eng = GeoPackageEngine(path=other)
    eng.open()
    try:
        conn = eng._get_conn()  # noqa: SLF001
        conn.execute('CREATE TABLE "parcels"(fid INTEGER PRIMARY KEY)')
        conn.commit()
    finally:
        eng.close()

    cfg = load_config(valid_yaml, gpkg_override=other)
    assert Path(cfg.gpkg) == other.resolve()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_invalid_yaml_syntax_rejected(tracked_gpkg: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\ngpkg: ./fixture.gpkg\ntriggers: [\n - foo: bar\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)


def test_unknown_top_level_key_rejected(tracked_gpkg: Path, tmp_path: Path) -> None:
    bad = tmp_path / "extra.yaml"
    bad.write_text(
        f"version: 1\ngpkg: {tracked_gpkg}\ntriggers: []\nunknown_key: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="invalid config schema"):
        load_config(bad)


def test_unknown_action_type_rejected(tracked_gpkg: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad_action.yaml"
    bad.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: t
                table: parcels
                actions:
                  - type: send_email_to_mars
            """,
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(bad)


def test_path_traversal_in_gpkg_rejected(valid_yaml: Path, tracked_gpkg: Path) -> None:
    """An override path that escapes cwd ∪ $HOME is refused.

    /etc/passwd is outside any plausible anchor on a CI runner.
    """
    with pytest.raises(ConfigError, match="path traversal|escapes"):
        load_config(valid_yaml, gpkg_override="/etc/passwd")


def test_webhook_url_must_be_http_or_https(tracked_gpkg: Path, tmp_path: Path) -> None:
    bad = tmp_path / "scheme.yaml"
    bad.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: t
                table: parcels
                actions:
                  - type: webhook
                    url: file:///etc/passwd
            """,
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(bad)


def test_validate_against_gpkg_reports_missing_table(
    tracked_gpkg: Path, tmp_path: Path
) -> None:
    cfg = tmp_path / "missing.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: typo
                table: parcells   # <- typo
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    config = load_config(cfg)
    errors = validate_against_gpkg(config)
    assert errors
    assert any("parcells" in e and "not found" in e for e in errors), errors


def test_validate_reports_webhook_without_url(
    tracked_gpkg: Path, tmp_path: Path
) -> None:
    """A webhook action without a URL must surface a clear error.

    URL is optional in the pydantic model (so a `webhook` action can
    co-exist with other types in the YAML without requiring the field
    on every type), but `validate_against_gpkg` enforces it.
    """
    cfg = tmp_path / "noweb.yaml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: incomplete
                table: parcels
                actions:
                  - type: webhook
            """,
        ).strip(),
        encoding="utf-8",
    )
    config = load_config(cfg)
    errors = validate_against_gpkg(config)
    assert any("webhook action requires url" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Static guarantee: yaml.load (unsafe) is never used
# ---------------------------------------------------------------------------


def test_module_does_not_call_unsafe_yaml_load() -> None:
    """Static grep: ensure the module never reaches for ``yaml.load``."""
    src = Path(__file__).resolve().parents[2] / "gispulse" / "runtime" / "config_loader.py"
    text = src.read_text(encoding="utf-8")
    # Reject ``yaml.load(`` exactly. ``yaml.safe_load`` is fine.
    bad_patterns = ["yaml.load(", "yaml.unsafe_load(", "yaml.full_load("]
    for pat in bad_patterns:
        assert pat not in text, (
            f"config_loader.py uses {pat!r} which is unsafe. "
            "Only yaml.safe_load is allowed."
        )


def test_empty_yaml_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    # Wording was generalised to ``empty config: <source>`` after #94
    # extracted ``parse_config_text`` (covers both file and inline).
    with pytest.raises(ConfigError, match="empty config"):
        load_config(empty)


def test_yaml_root_must_be_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(bad)


def test_predicate_dsl_compiled_into_ast(
    tracked_gpkg: Path, tmp_path: Path
) -> None:
    """A YAML predicate gets parsed eagerly and the compiled AST is
    stashed on the domain trigger's ``conditions``.
    """
    from gispulse.runtime.predicate_dsl import PredicateNode

    cfg_path = tmp_path / "with_predicate.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: high_value
                table: parcels
                predicate: "name == 'parcelle_1'"
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    triggers = to_triggers(cfg)
    assert len(triggers) == 1
    cond = triggers[0].conditions
    # Verbatim source is preserved for observability.
    assert cond["predicate"] == "name == 'parcelle_1'"
    # Compiled AST is ready for the runtime.
    ast = cond.get("predicate_ast")
    assert isinstance(ast, PredicateNode)


def test_predicate_with_invalid_dsl_rejected(
    tracked_gpkg: Path, tmp_path: Path
) -> None:
    """A broken DSL string surfaces as a config error with a clear
    pointer to the offending trigger."""
    cfg_path = tmp_path / "bad_predicate.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: broken
                table: parcels
                predicate: "1; DROP TABLE users"
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    msg = str(excinfo.value)
    assert "predicate parse failed" in msg


def test_predicate_optional_keeps_legacy_behaviour(
    tracked_gpkg: Path, tmp_path: Path
) -> None:
    """A YAML config without a ``predicate:`` key must continue to
    work — no AST set on conditions, runtime falls back to always-match."""
    cfg_path = tmp_path / "no_predicate.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: legacy_no_predicate
                table: parcels
                actions:
                  - type: log_event
            """,
        ).strip(),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    triggers = to_triggers(cfg)
    assert "predicate" not in triggers[0].conditions
    assert "predicate_ast" not in triggers[0].conditions


def test_when_dedupes_and_rejects_empty(tracked_gpkg: Path, tmp_path: Path) -> None:
    # Empty when -> error
    bad = tmp_path / "empty_when.yaml"
    bad.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: t
                table: parcels
                when: []
                actions: []
            """,
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(bad)

    # Duplicates collapsed
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        textwrap.dedent(
            f"""
            version: 1
            gpkg: {tracked_gpkg}
            triggers:
              - name: t
                table: parcels
                when: [INSERT, INSERT, UPDATE]
                actions: []
            """,
        ).strip(),
        encoding="utf-8",
    )
    cfg = load_config(dup)
    assert cfg.triggers[0].when == ["INSERT", "UPDATE"]
