from datetime import date, time
from pathlib import Path

import pytest

from stock_harness.config import load_provider_token, load_runtime_settings
from stock_harness.models import FuturesExchange


def test_legacy_provider_config_keeps_futures_disabled(tmp_path: Path) -> None:
    settings = _load(tmp_path, "")
    assert settings.futures.enabled is False
    assert settings.futures.exchanges == tuple(FuturesExchange)
    assert settings.futures.history_start == date(1990, 1, 1)
    assert settings.futures.canonical.provider == "tushare"
    assert settings.futures.provisional.provider == "akshare"
    assert settings.futures.provisional.fallback_provider is None


def test_futures_provider_configuration_is_independent_and_bounded(tmp_path: Path) -> None:
    settings = _load(
        tmp_path,
        """
  futures:
    enabled: true
    enabled_exchanges: [SHFE, DCE, SHFE]
    history_start: 2001-02-03
    correction_window_trading_days: 9
    canonical:
      provider: tushare
      token_env: FUTURES_TEST_TOKEN
      env_file: futures.env
      requests_per_minute: 88
      timeout_seconds: 12
      retries: 2
    provisional:
      provider: akshare
      refresh_interval_seconds: 5
      stale_after_seconds: 10
      max_contracts: 900
      fallback_provider: akshare-realtime
""",
    )
    assert settings.futures.enabled is True
    assert settings.futures.exchanges == (FuturesExchange.SHFE, FuturesExchange.DCE)
    assert settings.futures.history_start == date(2001, 2, 3)
    assert settings.futures.correction_window_trading_days == 9
    assert settings.futures.canonical.token_env == "FUTURES_TEST_TOKEN"
    assert settings.futures.canonical.env_file == tmp_path / "futures.env"
    assert settings.futures.canonical.requests_per_minute == 88
    assert settings.futures.provisional.refresh_interval_seconds == 10
    assert settings.futures.provisional.stale_after_seconds == 30
    assert settings.futures.provisional.max_contracts == 500
    assert settings.futures.provisional.fallback_provider == "akshare-realtime"
    assert settings.futures.provisional.session_rules


def test_futures_product_sessions_are_parsed_and_can_cross_midnight(tmp_path: Path) -> None:
    settings = _load(
        tmp_path,
        """
  futures:
    provisional:
      sessions:
        SHFE:
          "*": {day: ["09:00-10:15", "10:30-11:30", "13:30-15:00"]}
          CU: {night: ["21:00-01:00"]}
""",
    )
    wildcard, copper = settings.futures.provisional.session_rules
    assert wildcard.product_code == "*"
    assert len(wildcard.day) == 3
    assert copper.product_code == "CU"
    assert copper.night[0].start == time(21)
    assert copper.night[0].end == time(1)


def test_futures_token_uses_its_own_environment_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _load(
        tmp_path,
        """
  futures:
    enabled: true
    canonical: {token_env: FUTURES_ONLY_TOKEN}
""",
    )
    monkeypatch.setenv("TUSHARE_TOKEN", "stock-secret")
    monkeypatch.setenv("FUTURES_ONLY_TOKEN", "futures-secret")
    assert load_provider_token(settings.futures.canonical) == "futures-secret"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("enabled_exchanges: [UNKNOWN]", "unsupported futures exchange"),
        ("history_start: 20260820", "configuration date must use YYYY-MM-DD"),
        ("canonical: {provider: other}", "unsupported futures canonical provider"),
        ("provisional: {provider: other}", "unsupported futures provisional provider"),
        (
            "provisional: {fallback_provider: silent-other}",
            "unsupported futures provisional fallback",
        ),
        ("provisional: {sessions: []}", "sessions must be a mapping"),
        (
            "provisional: {sessions: {UNKNOWN: {'*': {day: []}}}}",
            "unsupported futures session exchange",
        ),
        (
            "provisional: {sessions: {SHFE: {CU: {night: ['21:00']}}}}",
            "invalid futures session window",
        ),
    ],
)
def test_invalid_futures_provider_contract_is_rejected(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _load(tmp_path, f"  futures: {{{body}}}")


def _load(tmp_path: Path, futures_section: str):
    provider_config = tmp_path / "providers.yaml"
    storage_config = tmp_path / "storage.yaml"
    provider_config.write_text(
        f"""
providers:
  default: tushare
  tushare: {{enabled: true, token_env: STOCK_TEST_TOKEN}}
  auto_update: {{}}
  intraday: {{}}
  etf_holdings: {{}}
  universes: {{etfs: [], broad_indices: []}}
  validation: {{}}
  repair: {{}}
{futures_section}
""".strip(),
        encoding="utf-8",
    )
    storage_config.write_text("storage: {database_path: market.sqlite}", encoding="utf-8")
    return load_runtime_settings(provider_config, storage_config)
