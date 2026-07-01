"""Cloud cover for the sky backdrop — parsing + best-effort fallback. Injected http_get."""

from ems.weather import cloud_cover_pct


def test_parses_cloud_cover():
    assert cloud_cover_pct(52.0, 5.0, http_get=lambda *a: {"current": {"cloud_cover": 80}}) == 80.0


def test_clamps_out_of_range():
    over = cloud_cover_pct(52.0, 5.0, http_get=lambda *a: {"current": {"cloud_cover": 140}})
    assert over == 100.0


def test_none_on_missing_field():
    assert cloud_cover_pct(52.0, 5.0, http_get=lambda *a: {"current": {}}) is None


def test_none_on_transport_error():
    def boom(*a):
        raise RuntimeError("offline")

    assert cloud_cover_pct(52.0, 5.0, http_get=boom) is None
