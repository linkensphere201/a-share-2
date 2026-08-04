"""Runtime configuration with secret values loaded from ignored environment files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class TushareSettings:
    token_env: str
    env_file: Path | None
    requests_per_minute: float
    retries: int
    retry_wait_seconds: float
    backoff_multiplier: float
    api_url: str = "http://api.waditu.com/dataapi"
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ValidationSettings:
    providers: tuple[str, ...]
    sample_symbols: tuple[str, ...]
    price_abs_tolerance: float
    volume_rel_tolerance: float


@dataclass(frozen=True, slots=True)
class RepairSettings:
    enabled: bool
    universe_provider: str
    fallback_providers: tuple[str, ...]
    max_dates_per_run: int


@dataclass(frozen=True, slots=True)
class UniverseSymbol:
    symbol: str
    category: str
    reason: str


@dataclass(frozen=True, slots=True)
class UniverseSettings:
    etfs: tuple[UniverseSymbol, ...]
    broad_indices: tuple[UniverseSymbol, ...]
    sector_source: str
    sector_level: str


@dataclass(frozen=True, slots=True)
class AutoUpdateSettings:
    enabled: bool
    poll_interval_seconds: int
    calendar_lookback_days: int


@dataclass(frozen=True, slots=True)
class IntradaySettings:
    enabled: bool
    poll_interval_seconds: int
    request_timeout_seconds: float
    stale_after_seconds: int
    circuit_breaker_failures: int
    circuit_breaker_seconds: int
    max_symbols: int


@dataclass(frozen=True, slots=True)
class EtfHoldingSettings:
    enabled: bool
    max_symbols_per_run: int
    lookback_open_dates: int


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    provider_name: str
    tushare: TushareSettings
    database_path: Path
    sqlite_cache_size_kib: int
    sqlite_mmap_size_mib: int
    sqlite_temp_store: str
    sqlite_busy_timeout_ms: int
    validation: ValidationSettings
    repair: RepairSettings
    universe: UniverseSettings
    auto_update: AutoUpdateSettings
    intraday: IntradaySettings
    etf_holdings: EtfHoldingSettings


def load_runtime_settings(provider_config: Path, storage_config: Path) -> RuntimeSettings:
    provider_data = _read_yaml(provider_config)
    storage_data = _read_yaml(storage_config)
    providers = _mapping(provider_data, "providers")
    provider_name = str(providers.get("default", "tushare"))
    if provider_name != "tushare":
        raise ValueError(f"unsupported default provider: {provider_name}")
    tushare = _mapping(providers, "tushare")
    if not bool(tushare.get("enabled", False)):
        raise ValueError("tushare provider is disabled")
    env_file_value = tushare.get("env_file")
    env_file = _resolve_path(provider_config, str(env_file_value)) if env_file_value else None
    validation = providers.get("validation", {})
    if not isinstance(validation, dict):
        raise ValueError("configuration section must be a mapping: providers.validation")
    repair = providers.get("repair", {})
    if not isinstance(repair, dict):
        raise ValueError("configuration section must be a mapping: providers.repair")
    universes = providers.get("universes", {})
    if not isinstance(universes, dict):
        raise ValueError("configuration section must be a mapping: providers.universes")
    auto_update = providers.get("auto_update", {})
    if not isinstance(auto_update, dict):
        raise ValueError("configuration section must be a mapping: providers.auto_update")
    intraday = providers.get("intraday", {})
    if not isinstance(intraday, dict):
        raise ValueError("configuration section must be a mapping: providers.intraday")
    etf_holdings = providers.get("etf_holdings", {})
    if not isinstance(etf_holdings, dict):
        raise ValueError("configuration section must be a mapping: providers.etf_holdings")
    storage = _mapping(storage_data, "storage")
    database_path = _resolve_path(storage_config, str(storage.get("database_path", "../data/market.sqlite")))
    sqlite_cache_size_kib = int(storage.get("sqlite_cache_size_kib", 32_768))
    sqlite_mmap_size_mib = int(storage.get("sqlite_mmap_size_mib", 256))
    sqlite_temp_store = str(storage.get("sqlite_temp_store", "MEMORY")).upper()
    sqlite_busy_timeout_ms = int(storage.get("sqlite_busy_timeout_ms", 120_000))
    if sqlite_cache_size_kib <= 0:
        raise ValueError("storage.sqlite_cache_size_kib must be positive")
    if sqlite_mmap_size_mib < 0:
        raise ValueError("storage.sqlite_mmap_size_mib must be non-negative")
    if sqlite_temp_store not in {"MEMORY", "FILE"}:
        raise ValueError("storage.sqlite_temp_store must be MEMORY or FILE")
    if sqlite_busy_timeout_ms <= 0:
        raise ValueError("storage.sqlite_busy_timeout_ms must be positive")
    return RuntimeSettings(
        provider_name=provider_name,
        tushare=TushareSettings(
            token_env=str(tushare.get("token_env", "TUSHARE_TOKEN")),
            env_file=env_file,
            requests_per_minute=float(tushare.get("requests_per_minute", 300)),
            retries=int(tushare.get("retries", 4)),
            retry_wait_seconds=float(tushare.get("retry_wait_seconds", 15)),
            backoff_multiplier=float(tushare.get("backoff_multiplier", 2)),
            api_url=str(tushare.get("api_url", "http://api.waditu.com/dataapi")),
            timeout_seconds=float(tushare.get("timeout_seconds", 30)),
        ),
        database_path=database_path,
        sqlite_cache_size_kib=sqlite_cache_size_kib,
        sqlite_mmap_size_mib=sqlite_mmap_size_mib,
        sqlite_temp_store=sqlite_temp_store,
        sqlite_busy_timeout_ms=sqlite_busy_timeout_ms,
        validation=ValidationSettings(
            providers=tuple(str(item) for item in validation.get("providers", ("akshare", "baostock"))),
            sample_symbols=tuple(
                str(item)
                for item in validation.get(
                    "sample_symbols", ("000001.SZ", "000002.SZ", "600000.SH")
                )
            ),
            price_abs_tolerance=float(validation.get("price_abs_tolerance", 0.001)),
            volume_rel_tolerance=float(validation.get("volume_rel_tolerance", 0.001)),
        ),
        repair=RepairSettings(
            enabled=bool(repair.get("enabled", True)),
            universe_provider=str(repair.get("universe_provider", "baostock")),
            fallback_providers=tuple(
                str(item) for item in repair.get("fallback_providers", ("akshare",))
            ),
            max_dates_per_run=int(repair.get("max_dates_per_run", 1)),
        ),
        universe=UniverseSettings(
            etfs=_universe_symbols(universes, "etfs"),
            broad_indices=_universe_symbols(universes, "broad_indices"),
            sector_source=str(universes.get("sector_source", "SW2021")),
            sector_level=str(universes.get("sector_level", "L1")),
        ),
        auto_update=AutoUpdateSettings(
            enabled=bool(auto_update.get("enabled", True)),
            poll_interval_seconds=max(
                60, int(auto_update.get("poll_interval_seconds", 900))
            ),
            calendar_lookback_days=max(
                7, int(auto_update.get("calendar_lookback_days", 14))
            ),
        ),
        intraday=IntradaySettings(
            enabled=bool(intraday.get("enabled", True)),
            poll_interval_seconds=max(10, int(intraday.get("poll_interval_seconds", 30))),
            request_timeout_seconds=max(1.0, float(intraday.get("request_timeout_seconds", 8))),
            stale_after_seconds=max(30, int(intraday.get("stale_after_seconds", 90))),
            circuit_breaker_failures=max(1, int(intraday.get("circuit_breaker_failures", 3))),
            circuit_breaker_seconds=max(30, int(intraday.get("circuit_breaker_seconds", 60))),
            max_symbols=max(1, min(5000, int(intraday.get("max_symbols", 1000)))),
        ),
        etf_holdings=EtfHoldingSettings(
            enabled=bool(etf_holdings.get("enabled", True)),
            max_symbols_per_run=max(1, int(etf_holdings.get("max_symbols_per_run", 25))),
            lookback_open_dates=max(1, int(etf_holdings.get("lookback_open_dates", 5))),
        ),
    )


def load_provider_token(settings: TushareSettings) -> str:
    token = os.environ.get(settings.token_env)
    if token:
        return token
    if settings.env_file is not None:
        _load_env_file(settings.env_file)
        token = os.environ.get(settings.token_env)
    if not token:
        raise ValueError(f"missing provider credential: {settings.token_env}")
    return token


def _load_env_file(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"provider env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise ValueError(f"configuration file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return data


def _mapping(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"configuration section must be a mapping: {key}")
    return value


def _universe_symbols(data: dict[str, object], key: str) -> tuple[UniverseSymbol, ...]:
    value = data.get(key, ())
    if not isinstance(value, list):
        raise ValueError(f"configuration section must be a list: providers.universes.{key}")
    symbols: list[UniverseSymbol] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"universe entries must be mappings: providers.universes.{key}")
        symbol = str(item.get("symbol", "")).strip().upper()
        category = str(item.get("category", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not symbol or not category or not reason:
            raise ValueError(f"universe entries require symbol, category, and reason: {key}")
        if symbol in seen:
            raise ValueError(f"duplicate universe symbol: {symbol}")
        seen.add(symbol)
        symbols.append(UniverseSymbol(symbol, category, reason))
    return tuple(symbols)


def _resolve_path(config_path: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()
