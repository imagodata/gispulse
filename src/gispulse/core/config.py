"""
Centralised configuration for GISPulse.

Single source of truth for all settings.  Values are loaded with the
following precedence (highest wins):

1. **Environment variables** — ``GISPULSE_*`` prefix, flat names.
2. **TOML file** — ``gispulse.toml`` in the working directory (optional).
3. **Defaults** — hardcoded in the Pydantic models below.

Usage::

    from gispulse.core.config import settings

    if settings.engine.backend == "postgis":
        ...

All existing ``GISPULSE_*`` env vars keep working as-is for backward
compatibility.  The flat env var names are mapped to the nested structure
via ``model_config`` aliases and ``@model_validator`` hooks.

TOML support is optional — if no ``gispulse.toml`` is found, everything
works exactly like before (pure env vars + defaults).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# TOML loader (optional, Python 3.11+ has tomllib builtin)
# ---------------------------------------------------------------------------

def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return its contents as a dict.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    if not path.is_file():
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib  # type: ignore[import-not-found]
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    return {}
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _find_toml() -> Path:
    """Locate the base TOML config file.

    Search order:
    1. ``GISPULSE_CONFIG`` env var (explicit path)
    2. ``gispulse.toml`` in the current working directory
    3. ``~/.gispulse/gispulse.toml`` (user-level)
    """
    explicit = os.environ.get("GISPULSE_CONFIG", "").strip()
    if explicit:
        return Path(explicit)

    cwd_toml = Path.cwd() / "gispulse.toml"
    if cwd_toml.is_file():
        return cwd_toml

    return Path.home() / ".gispulse" / "gispulse.toml"


def _find_profile_toml(base_path: Path) -> Path | None:
    """Locate the profile-specific TOML overlay.

    When ``GISPULSE_PROFILE`` is set (e.g. ``prod``), searches for a
    profile file derived from the base filename.  For a base file named
    ``gispulse.toml``, the profile file is ``gispulse.prod.toml``.
    For ``custom.toml``, it's ``custom.prod.toml``.

    Search order:
    1. Next to the base config file
    2. Current working directory
    3. ``~/.gispulse/``

    Returns ``None`` if no profile is active or the file doesn't exist.
    """
    profile = os.environ.get("GISPULSE_PROFILE", "").strip()
    if not profile:
        return None

    # Derive profile filename: base.toml → base.{profile}.toml
    stem = base_path.stem  # "gispulse" from "gispulse.toml"
    profile_name = f"{stem}.{profile}.toml"

    candidates = [
        base_path.parent / profile_name,
        Path.cwd() / profile_name,
        Path.home() / ".gispulse" / profile_name,
    ]
    # Also check the standard name if base has a non-standard name
    if stem != "gispulse":
        candidates.extend([
            base_path.parent / f"gispulse.{profile}.toml",
            Path.cwd() / f"gispulse.{profile}.toml",
            Path.home() / ".gispulse" / f"gispulse.{profile}.toml",
        ])

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base* (overlay wins)."""
    result = dict(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# Module-level TOML data — reloaded when paths change
_TOML_CACHE_KEY: tuple | None = None
_TOML_DATA: dict[str, Any] = {}


def _get_toml_data() -> dict[str, Any]:
    """Return the merged TOML data (base + profile), loading lazily.

    The merge order is: base ``gispulse.toml`` < profile
    ``gispulse.{PROFILE}.toml``.  Profile values override base values.
    """
    global _TOML_CACHE_KEY, _TOML_DATA
    base_path = _find_toml()
    profile_path = _find_profile_toml(base_path)
    cache_key = (str(base_path), str(profile_path))
    if cache_key != _TOML_CACHE_KEY:
        _TOML_CACHE_KEY = cache_key
        base_data = _load_toml(base_path)
        if profile_path is not None:
            profile_data = _load_toml(profile_path)
            base_data = _deep_merge(base_data, profile_data)
        _TOML_DATA = base_data
    return _TOML_DATA


def _toml_section(section: str) -> dict[str, Any]:
    """Return a specific ``[section]`` from the TOML file, or ``{}``."""
    return _get_toml_data().get(section, {})


def _resolve(
    env_var: str,
    toml_section: str,
    toml_key: str,
    default: Any = "",
) -> Any:
    """Resolve a config value with precedence: env var > TOML > default.

    Args:
        env_var:       Full env var name (e.g. ``"GISPULSE_ENGINE"``).
        toml_section:  TOML ``[section]`` name (e.g. ``"engine"``).
        toml_key:      Key within the section (e.g. ``"backend"``).
        default:       Fallback value if neither env nor TOML provides one.
    """
    val = os.environ.get(env_var)
    if val is not None:
        return val
    toml = _toml_section(toml_section)
    if toml_key in toml:
        return toml[toml_key]
    return default


# ---------------------------------------------------------------------------
# Sub-models (grouped by domain)
# ---------------------------------------------------------------------------


class EngineSettings(BaseSettings):
    """Spatial engine selection and tier gating."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    backend: Literal["duckdb", "postgis", "hybrid", "gpkg"] = "gpkg"
    tier: Literal["community", "pro", "team", "enterprise"] = "community"
    license_key: str = ""
    licence_skip_verify: bool = False
    licence_public_key: str = (
        "MCowBQYDK2VwAyEAGISPULSE_DEFAULT_KEY_REPLACE_IN_PROD_00000000="
    )
    demo_mode: bool = False
    demo_token: str = ""
    demo_token_sha256: str = ""

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "engine"
        if "backend" not in values and "ENGINE" not in values:
            values["backend"] = _resolve("GISPULSE_ENGINE", S, "backend", "gpkg")
        if "tier" not in values and "TIER" not in values:
            values["tier"] = _resolve("GISPULSE_TIER", S, "tier", "community")
        if "license_key" not in values:
            values["license_key"] = _resolve("GISPULSE_LICENSE_KEY", S, "license_key")
        if "licence_skip_verify" not in values:
            raw = _resolve("GISPULSE_LICENCE_SKIP_VERIFY", S, "licence_skip_verify", "")
            values["licence_skip_verify"] = (
                raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true")
            )
        if "licence_public_key" not in values:
            val = _resolve("GISPULSE_LICENCE_PUBLIC_KEY", S, "licence_public_key", "")
            if val:
                values["licence_public_key"] = val
        if "demo_mode" not in values:
            raw = _resolve("GISPULSE_DEMO_MODE", S, "demo_mode", "")
            values["demo_mode"] = (
                raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true")
            )
        if "demo_token" not in values:
            values["demo_token"] = _resolve("GISPULSE_DEMO_TOKEN", S, "demo_token", "")
        if "demo_token_sha256" not in values:
            values["demo_token_sha256"] = _resolve(
                "GISPULSE_DEMO_TOKEN_SHA256", S, "demo_token_sha256", ""
            )
        return values

    @field_validator("tier", mode="before")
    @classmethod
    def _normalize_tier(cls, v: str) -> str:
        v = str(v).lower().strip()
        if v not in ("community", "pro", "team", "enterprise"):
            return "community"
        return v


class DatabaseSettings(BaseSettings):
    """PostgreSQL / DuckDB / GPKG connection."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    dsn: str = ""
    gpkg_path: str = "project.gpkg"
    postgis_dsn: str = ""
    base_dsn: str = ""

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "database"
        if "dsn" not in values:
            # GISPULSE_DSN takes priority, then GISPULSE_DATABASE_URL, then TOML
            val = os.environ.get("GISPULSE_DSN") or os.environ.get("GISPULSE_DATABASE_URL")
            values["dsn"] = val if val is not None else _toml_section(S).get("dsn", "")
        if "gpkg_path" not in values:
            values["gpkg_path"] = _resolve("GISPULSE_GPKG_PATH", S, "gpkg_path", "project.gpkg")
        if "postgis_dsn" not in values:
            values["postgis_dsn"] = _resolve("GISPULSE_POSTGIS_DSN", S, "postgis_dsn")
        if "base_dsn" not in values:
            values["base_dsn"] = _resolve("GISPULSE_BASE_DSN", S, "base_dsn")
        return values


class StorageSettings(BaseSettings):
    """Persistence storage (SQLite metadata + file storage)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    mode: Literal["sqlite", "memory"] = "sqlite"
    db_path: Path = Path.home() / ".gispulse" / "gispulse.db"
    data_dir: str = "~/.gispulse/data"

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "storage"
        if "mode" not in values:
            values["mode"] = _resolve("GISPULSE_STORAGE", S, "mode", "sqlite")
        if "db_path" not in values:
            val = _resolve("GISPULSE_DB_PATH", S, "db_path", "")
            if val:
                values["db_path"] = val
        if "data_dir" not in values:
            values["data_dir"] = _resolve("GISPULSE_DATA_DIR", S, "data_dir", "~/.gispulse/data")
        return values


class S3Settings(BaseSettings):
    """S3/MinIO object storage (Pro tier)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_S3_")

    endpoint: str = ""
    bucket: str = "gispulse"
    access_key: str = ""
    secret_key: str = ""
    region: str = "us-east-1"

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "s3"
        if "endpoint" not in values:
            values["endpoint"] = _resolve("GISPULSE_S3_ENDPOINT", S, "endpoint")
        if "bucket" not in values:
            values["bucket"] = _resolve("GISPULSE_S3_BUCKET", S, "bucket", "gispulse")
        if "access_key" not in values:
            values["access_key"] = _resolve("GISPULSE_S3_ACCESS_KEY", S, "access_key")
        if "secret_key" not in values:
            values["secret_key"] = _resolve("GISPULSE_S3_SECRET_KEY", S, "secret_key")
        if "region" not in values:
            values["region"] = _resolve("GISPULSE_S3_REGION", S, "region", "us-east-1")
        return values


class ApiSettings(BaseSettings):
    """HTTP API authentication and behaviour."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    env: Literal["development", "production"] = "development"
    api_keys: str = ""
    api_key: str = ""  # legacy singular form
    cors_origins: str = ""
    rbac: bool = False
    max_upload_mb: int = 500
    metrics_token: str = ""
    sql_admin_key: str = ""
    read_only: bool = False

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "api"
        if "env" not in values:
            values["env"] = _resolve("GISPULSE_ENV", S, "env", "development")
        if "api_keys" not in values:
            values["api_keys"] = _resolve("GISPULSE_API_KEYS", S, "api_keys")
        if "api_key" not in values:
            values["api_key"] = _resolve("GISPULSE_API_KEY", S, "api_key")
        if "cors_origins" not in values:
            values["cors_origins"] = _resolve("GISPULSE_CORS_ORIGINS", S, "cors_origins")
        if "rbac" not in values:
            raw = _resolve("GISPULSE_RBAC", S, "rbac", "")
            values["rbac"] = (
                raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
            )
        if "max_upload_mb" not in values:
            try:
                val = int(_resolve("GISPULSE_MAX_UPLOAD_MB", S, "max_upload_mb", "500"))
                values["max_upload_mb"] = val if 0 < val <= 5000 else 500
            except (ValueError, TypeError):
                values["max_upload_mb"] = 500
        if "metrics_token" not in values:
            values["metrics_token"] = _resolve("GISPULSE_METRICS_TOKEN", S, "metrics_token")
        if "sql_admin_key" not in values:
            values["sql_admin_key"] = _resolve("GISPULSE_SQL_ADMIN_KEY", S, "sql_admin_key")
        if "read_only" not in values:
            raw = _resolve("GISPULSE_READ_ONLY", S, "read_only", "")
            values["read_only"] = (
                raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
            )
        return values

    def get_api_keys_set(self) -> set[str] | None:
        """Return the parsed set of API keys, or None if auth is disabled."""
        keys = {k.strip() for k in self.api_keys.split(",") if k.strip()}
        if self.api_key.strip():
            keys.add(self.api_key.strip())
        return keys if keys else None


class OidcSettings(BaseSettings):
    """OpenID Connect SSO (Enterprise tier)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_OIDC_")

    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: str = "openid,profile,email"
    default_role: str = "editor"

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        # TOML section is [auth.oidc] (nested) — read from "auth" first
        toml_oidc = _get_toml_data().get("auth", {}).get("oidc", {})
        S = "oidc"  # fallback flat section
        if "issuer" not in values:
            values["issuer"] = os.environ.get("GISPULSE_OIDC_ISSUER") or toml_oidc.get("issuer", _toml_section(S).get("issuer", ""))
        if "client_id" not in values:
            values["client_id"] = os.environ.get("GISPULSE_OIDC_CLIENT_ID") or toml_oidc.get("client_id", _toml_section(S).get("client_id", ""))
        if "client_secret" not in values:
            values["client_secret"] = os.environ.get("GISPULSE_OIDC_CLIENT_SECRET") or toml_oidc.get("client_secret", "")
        if "redirect_uri" not in values:
            values["redirect_uri"] = os.environ.get("GISPULSE_OIDC_REDIRECT_URI") or toml_oidc.get("redirect_uri", "")
        if "scopes" not in values:
            values["scopes"] = os.environ.get("GISPULSE_OIDC_SCOPES") or toml_oidc.get("scopes", "openid,profile,email")
        if "default_role" not in values:
            values["default_role"] = os.environ.get("GISPULSE_OIDC_DEFAULT_ROLE") or toml_oidc.get("default_role", "editor")
        return values

    @property
    def enabled(self) -> bool:
        return bool(self.issuer.strip())

    @property
    def scopes_list(self) -> list[str]:
        return [s.strip() for s in self.scopes.split(",") if s.strip()]


class SessionSettings(BaseSettings):
    """Session JWT signing."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_SESSION_")

    secret: str = ""
    expiry: int = 28800  # 8 hours

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "session"
        if "secret" not in values:
            values["secret"] = _resolve("GISPULSE_SESSION_SECRET", S, "secret")
        if "expiry" not in values:
            values["expiry"] = int(_resolve("GISPULSE_SESSION_EXPIRY", S, "expiry", "28800"))
        return values


class RedisSettings(BaseSettings):
    """Redis connection (distributed job queue, rate limiting, metering)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    url: str = ""
    rate_limit_storage: str = ""

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "redis"
        if "url" not in values:
            values["url"] = _resolve("GISPULSE_REDIS_URL", S, "url")
        if "rate_limit_storage" not in values:
            values["rate_limit_storage"] = _resolve("GISPULSE_RATE_LIMIT_STORAGE", S, "rate_limit_storage")
        return values

    @property
    def effective_rate_limit_uri(self) -> str:
        """Resolve rate limit storage URI with fallback chain."""
        if self.rate_limit_storage.strip():
            return self.rate_limit_storage.strip()
        if self.url.strip():
            return self.url.strip()
        return "memory://"


class LoggingSettings(BaseSettings):
    """Structured logging."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "logging"
        if "log_level" not in values:
            values["log_level"] = _resolve("GISPULSE_LOG_LEVEL", S, "level", "INFO")
        if "log_format" not in values:
            values["log_format"] = _resolve("GISPULSE_LOG_FORMAT", S, "format", "console")
        return values

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("log_format", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        v = v.lower()
        return v if v in ("console", "json") else "console"


class AuditSettings(BaseSettings):
    """Audit logging (Pro tier, opt-in)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_AUDIT")

    enabled: bool = False
    retention_days: int = 90

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "audit"
        if "enabled" not in values:
            raw = _resolve("GISPULSE_AUDIT", S, "enabled", "")
            values["enabled"] = (
                raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
            )
        if "retention_days" not in values:
            values["retention_days"] = int(
                _resolve("GISPULSE_AUDIT_RETENTION_DAYS", S, "retention_days", "90")
            )
        return values


class StripeSettings(BaseSettings):
    """Stripe billing (SaaS mode)."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_STRIPE_")

    api_key: str = ""
    webhook_secret: str = ""
    price_pro_monthly: str = ""
    price_pro_annual: str = ""
    price_team_monthly: str = ""
    price_team_annual: str = ""

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "stripe"
        if "api_key" not in values:
            values["api_key"] = _resolve("GISPULSE_STRIPE_API_KEY", S, "api_key")
        if "webhook_secret" not in values:
            values["webhook_secret"] = _resolve("GISPULSE_STRIPE_WEBHOOK_SECRET", S, "webhook_secret")
        if "price_pro_monthly" not in values:
            values["price_pro_monthly"] = _resolve("GISPULSE_STRIPE_PRICE_PRO_MONTHLY", S, "price_pro_monthly")
        if "price_pro_annual" not in values:
            values["price_pro_annual"] = _resolve("GISPULSE_STRIPE_PRICE_PRO_ANNUAL", S, "price_pro_annual")
        if "price_team_monthly" not in values:
            values["price_team_monthly"] = _resolve("GISPULSE_STRIPE_PRICE_TEAM_MONTHLY", S, "price_team_monthly")
        if "price_team_annual" not in values:
            values["price_team_annual"] = _resolve("GISPULSE_STRIPE_PRICE_TEAM_ANNUAL", S, "price_team_annual")
        return values

    def resolve_price_id(self, tier: str, interval: str) -> str:
        """Return the Stripe Price ID for a given tier and billing interval."""
        _valid = {
            ("pro", "month"): "price_pro_monthly",
            ("pro", "year"): "price_pro_annual",
            ("team", "month"): "price_team_monthly",
            ("team", "year"): "price_team_annual",
        }
        attr_name = _valid.get((tier, interval))
        if attr_name is None:
            raise ValueError(
                f"No price mapping for tier={tier!r}, interval={interval!r}. "
                f"Valid combinations: {list(_valid.keys())}"
            )
        price_id = getattr(self, attr_name, "")
        if not price_id:
            env_var = f"GISPULSE_STRIPE_{attr_name.upper()}"
            raise ValueError(f"{env_var} is not set")
        return price_id


class TelemetrySettings(BaseSettings):
    """Anonymous usage telemetry."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    telemetry: str = ""  # "0" or "1" override, empty = use file
    telemetry_url: str = ""
    no_update_check: bool = False

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "telemetry"
        if "telemetry" not in values:
            values["telemetry"] = _resolve("GISPULSE_TELEMETRY", S, "enabled")
        if "telemetry_url" not in values:
            values["telemetry_url"] = _resolve("GISPULSE_TELEMETRY_URL", S, "url")
        if "no_update_check" not in values:
            raw = _resolve("GISPULSE_NO_UPDATE_CHECK", S, "no_update_check", "")
            values["no_update_check"] = (
                raw if isinstance(raw, bool) else str(raw) == "1"
            )
        return values


class JobSettings(BaseSettings):
    """Job execution tunables."""

    model_config = SettingsConfigDict(env_prefix="GISPULSE_")

    job_timeout: int = 3600
    duckdb_threshold: int = 100_000

    @model_validator(mode="before")
    @classmethod
    def _compat_env(cls, values: dict) -> dict:
        S = "jobs"
        if "job_timeout" not in values:
            values["job_timeout"] = int(_resolve("GISPULSE_JOB_TIMEOUT", S, "timeout", "3600"))
        if "duckdb_threshold" not in values:
            values["duckdb_threshold"] = int(_resolve("GISPULSE_DUCKDB_THRESHOLD", S, "duckdb_threshold", "100000"))
        return values


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root configuration object — single import, full access.

    Usage::

        from gispulse.core.config import settings

        settings.engine.backend   # "duckdb"
        settings.api.env          # "development"
        settings.redis.url        # ""
    """

    model_config = SettingsConfigDict(
        # No env_prefix on root — each sub-model handles its own env vars
        # through _compat_env validators.  This avoids GISPULSE_STORAGE
        # (a string) colliding with the `storage` field (a sub-model).
        env_prefix="GISPULSE_ROOT_",  # effectively unused prefix
        case_sensitive=False,
    )

    engine: EngineSettings = Field(default_factory=EngineSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    oidc: OidcSettings = Field(default_factory=OidcSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    stripe: StripeSettings = Field(default_factory=StripeSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    jobs: JobSettings = Field(default_factory=JobSettings)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, reloadable)
# ---------------------------------------------------------------------------

def get_settings() -> Settings:
    """Build a fresh Settings instance from current environment variables.

    Cheap enough to call per-request if needed (~1ms).  Most code should
    use the module-level ``settings`` proxy instead, which delegates here.
    """
    return Settings()


class _SettingsProxy:
    """Transparent proxy that delegates to :func:`get_settings` on every
    top-level attribute access.

    This keeps ``from core.config import settings`` ergonomic while
    ensuring that env-var changes made by test fixtures (or dynamic
    reconfiguration) are always picked up — no stale singleton.
    """

    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)

    def __repr__(self) -> str:
        return repr(get_settings())


settings: Settings = _SettingsProxy()  # type: ignore[assignment]
