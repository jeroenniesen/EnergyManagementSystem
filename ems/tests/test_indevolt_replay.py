"""Read-only EMS replay contracts.

These fixtures represent captured ``GetData`` frames, so adapter behavior can be checked against
realistic device payloads without contacting or writing to hardware.
"""

import pytest

from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient


def test_replayed_frames_preserve_signed_power_and_soc_over_a_cycle():
    frames = iter(
        [
            {"6002": "61", "6000": "1200", "6001": 1001, "firmware": "gen2"},
            {"6002": 62, "6000": 850, "6001": 1002, "firmware": "gen2"},
            {"6002": 62, "6000": 0, "6001": 1000, "firmware": "gen2"},
        ]
    )
    client = IndevoltReadClient("replay", rpc_post=lambda _keys: next(frames))

    assert client.read_power_soc() == (-1200.0, 61.0)
    assert client.read_power_soc() == (850.0, 62.0)
    assert client.read_power_soc() == (0.0, 62.0)


@pytest.mark.parametrize(
    "frame",
    [
        {"6002": 55, "6000": "not-a-number", "6001": 1002},
        {"6002": 55, "6001": 1002},
        {"6002": None, "6000": 400, "6001": 1002},
    ],
)
def test_replay_rejects_unusable_frames_instead_of_inventing_state(frame):
    with pytest.raises(BatteryUnavailable):
        IndevoltReadClient("replay", rpc_post=lambda _keys: frame).read_power_soc()


def test_replay_client_requests_only_read_registers():
    requested: list[tuple[int, ...]] = []

    def capture(keys):
        requested.append(tuple(keys))
        return {"6002": 50, "6000": 0, "6001": 1000}

    IndevoltReadClient("replay", rpc_post=capture).read_power_soc()
    assert requested == [(6002, 6000, 6001)]
