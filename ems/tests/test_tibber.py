from ems.sources.tibber import TibberPriceSource, parse_price_info

# Shape of a real Tibber priceInfo response (trimmed): hourly total in EUR/kWh + tz-aware startsAt.
DATA = {
    "viewer": {
        "homes": [
            {
                "currentSubscription": {
                    "priceInfo": {
                        "today": [
                            {"total": 0.2412, "startsAt": "2026-06-28T00:00:00+02:00"},
                            {"total": 0.1987, "startsAt": "2026-06-28T01:00:00+02:00"},
                        ],
                        "tomorrow": [
                            {"total": 0.3055, "startsAt": "2026-06-29T00:00:00+02:00"},
                        ],
                    }
                }
            }
        ]
    }
}


def test_parse_expands_each_hour_into_four_15min_slots():
    slots = parse_price_info(DATA)
    assert len(slots) == 3 * 4  # 2 today + 1 tomorrow hours, each -> 4 quarter-hours
    # First hour expands to :00/:15/:30/:45, all at the same price.
    first4 = slots[:4]
    assert [s.start.minute for s in first4] == [0, 15, 30, 45]
    assert all(s.eur_per_kwh == 0.2412 for s in first4)
    # tz-aware, sorted ascending, 15-min spacing.
    assert all(s.start.tzinfo is not None for s in slots)
    assert slots == sorted(slots, key=lambda s: s.start)
    assert (slots[1].start - slots[0].start).total_seconds() == 900


def test_parse_tolerates_missing_pieces():
    assert parse_price_info({}) == []
    assert parse_price_info({"viewer": {"homes": []}}) == []
    assert parse_price_info({"viewer": {"homes": [{}]}}) == []
    assert parse_price_info(DATA, home_index=-1) == []  # negative index not silently last-home


def test_parse_skips_one_malformed_entry_but_keeps_the_rest():
    bad = {
        "viewer": {"homes": [{"currentSubscription": {"priceInfo": {"today": [
            {"total": "not-a-number", "startsAt": "2026-06-28T00:00:00+02:00"},
            {"total": 0.20, "startsAt": "2026-06-28T01:00:00+02:00"},
        ], "tomorrow": []}}}]}
    }
    slots = parse_price_info(bad)
    assert len(slots) == 4  # only the good hour survives
    assert all(s.eur_per_kwh == 0.20 for s in slots)


def test_source_uses_injected_transport():
    calls = {}

    def fake_post(url, token, body):
        calls["url"], calls["token"], calls["body"] = url, token, body
        return DATA

    src = TibberPriceSource("tok-123", http_post=fake_post)
    slots = src.slots()
    assert len(slots) == 12
    assert calls["token"] == "tok-123"
    assert "priceInfo" in calls["body"]["query"]


def test_empty_token_returns_no_slots():
    assert TibberPriceSource("").slots() == []


def test_graphql_error_degrades_to_empty():
    def boom(url, token, body):
        raise RuntimeError("Tibber GraphQL error: invalid token")

    assert TibberPriceSource("bad", http_post=boom).slots() == []  # graceful, no raise
