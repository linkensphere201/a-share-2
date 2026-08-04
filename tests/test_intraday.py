from datetime import date, datetime, timedelta, timezone

from stock_harness.config import IntradaySettings
from stock_harness.intraday import IntradayQuoteService, is_market_polling_time
from stock_harness.models import ProvisionalDailyBar


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


def test_subscription_removes_unreferenced_cached_symbols():
    provider = FakeProvider()
    service = IntradayQuoteService(_settings(), lambda _day: True, provider)
    service.subscribe("group-1", ["600519.SH", "000001.SZ"])
    service.refresh_once(datetime(2026, 8, 4, 10, 0, tzinfo=CHINA_TIME))

    service.subscribe("group-1", ["000001.SZ"])

    assert service.get("600519.SH") is None
    assert service.status()["symbol_count"] == 1
