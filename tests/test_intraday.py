from datetime import date, datetime, timedelta, timezone

from stock_harness.config import IntradaySettings
from stock_harness.intraday import IntradayQuoteService, is_market_polling_time
from stock_harness.models import ProvisionalDailyBar
from stock_harness.sqlite_store import SQLiteMarketDataStore


CHINA_TIME = timezone(timedelta(hours=8))


class FakeProvider:
    code = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.error: Exception | None = None

    def fetch(self, symbols):
        self.calls.append(tuple(symbols))
        if self.error:
            raise self.error
        now = datetime(2026, 8, 4, 14, 30, tzinfo=CHINA_TIME)
        return [
            ProvisionalDailyBar(
                symbol=symbol,
                trade_date=now.date(),
                open=10,
                high=12,
                low=9,
                close=11,
                volume=1000,
                amount=10000,
                previous_close=10,
                change_percent=10,
                source=self.code,
                provider_time=now,
                received_at=now,
            )
            for symbol in symbols
        ]


def _settings() -> IntradaySettings:
    return IntradaySettings(True, 30, 8, 90, 2, 60, 1000)


def test_market_polling_time_excludes_lunch_and_after_close():
    assert is_market_polling_time(datetime(2026, 8, 4, 10, 0, tzinfo=CHINA_TIME))
    assert not is_market_polling_time(datetime(2026, 8, 4, 12, 0, tzinfo=CHINA_TIME))
    assert not is_market_polling_time(datetime(2026, 8, 4, 15, 1, tzinfo=CHINA_TIME))


def test_service_polls_only_during_trading_session_and_keeps_cache_on_failure():
    provider = FakeProvider()
    service = IntradayQuoteService(_settings(), lambda _day: True, provider)
    service.subscribe("group-1", ["600519.SH", "600519.SH"])

    service.refresh_once(datetime(2026, 8, 4, 14, 30, tzinfo=CHINA_TIME))
    assert provider.calls == [("600519.SH",)]
    assert service.get("600519.SH", datetime(2026, 8, 4, 14, 30, tzinfo=CHINA_TIME))["close"] == 11

    provider.error = RuntimeError("temporary upstream failure")
    service.refresh_once(datetime(2026, 8, 4, 14, 31, tzinfo=CHINA_TIME))
    assert service.status()["state"] == "error"
    assert service.get("600519.SH", datetime(2026, 8, 4, 14, 31, tzinfo=CHINA_TIME))["stale"] is False

    service.refresh_once(datetime(2026, 8, 4, 16, 0, tzinfo=CHINA_TIME))
    assert len(provider.calls) == 2
    assert service.status()["state"] == "market_closed"


def test_subscription_keeps_unreferenced_cached_symbols():
    provider = FakeProvider()
    service = IntradayQuoteService(_settings(), lambda _day: True, provider)
    service.subscribe("group-1", ["600519.SH", "000001.SZ"])
    service.refresh_once(datetime(2026, 8, 4, 10, 0, tzinfo=CHINA_TIME))

    service.subscribe("group-1", ["000001.SZ"])

    assert service.get("600519.SH") is not None
    assert service.status()["symbol_count"] == 1


def test_manual_refresh_fetches_only_requested_symbols_without_changing_subscription():
    provider = FakeProvider()
    service = IntradayQuoteService(_settings(), lambda _day: True, provider)
    service.subscribe("group-1", ["600519.SH", "000001.SZ"])

    items = service.refresh_symbols(
        ["510300.SH"], datetime(2026, 8, 4, 14, 30, tzinfo=CHINA_TIME)
    )

    assert provider.calls == [("510300.SH",)]
    assert [item["symbol"] for item in items] == ["510300.SH"]
    assert service.status()["symbol_count"] == 2


def test_persisted_provisional_bar_survives_service_restart():
    provider = FakeProvider()
    store = SQLiteMarketDataStore(":memory:")
    first = IntradayQuoteService(_settings(), lambda _day: True, provider, store)
    first.subscribe("group-1", ["600519.SH"])
    first.refresh_once(datetime(2026, 8, 4, 14, 30, tzinfo=CHINA_TIME))

    restarted = IntradayQuoteService(_settings(), lambda _day: True, provider, store)
    restored = restarted.get(
        "600519.SH", datetime(2026, 8, 4, 14, 31, tzinfo=CHINA_TIME)
    )
    store.close()

    assert restored is not None
    assert restored["close"] == 11
    assert restored["bar_state"] == "intraday"


def test_manual_closed_session_warning_is_rate_limited(caplog):
    service = IntradayQuoteService(_settings(), lambda _day: True, FakeProvider())
    first = datetime(2026, 8, 4, 16, 0, tzinfo=CHINA_TIME)

    service.refresh_symbols(["600519.SH"], first)
    service.refresh_symbols(["600519.SH"], first + timedelta(seconds=30))

    messages = [record.message for record in caplog.records if "manual_refresh_skipped" in record.message]
    assert len(messages) == 1
