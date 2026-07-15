"""Regression tests for the Oura MCP server.

These target bugs this project has ACTUALLY shipped and fixed, plus the invariants
that make the CSV output safe for an agent to reason over. No network: `_fetch` is
monkeypatched, so tests run offline and in CI without a token.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("OURA_PERSONAL_ACCESS_TOKEN", "test-token-not-real")

import server  # noqa: E402


def _fake_fetch(sample: dict, truncated: bool = False):
    async def _f(client, path, params=None, **kw):
        return sample.get(path, []), truncated
    return _f


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# tool surface
# --------------------------------------------------------------------------


def test_all_tools_registered_and_read_only():
    tools = run(server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "oura_get_daily_summary", "oura_get_sleep_detail", "oura_get_sleep_time",
        "oura_get_readiness_detail", "oura_get_stress_resilience", "oura_get_workouts",
        "oura_get_baselines", "oura_get_heart_rate",
    }
    assert names == expected, f"tool surface drift: {names ^ expected}"
    for t in tools:
        assert t.annotations.readOnlyHint is True, f"{t.name} is not marked read-only"


# --------------------------------------------------------------------------
# 0.1.1 regression: explicit JSON nulls must render blank, never "None"
# --------------------------------------------------------------------------

NULL_SAMPLE = {
    "daily_readiness": [{"day": "2026-07-01", "score": None, "temperature_deviation": None,
                         "contributors": {"hrv_balance": None}}],
    "daily_sleep": [{"day": "2026-07-01", "score": None}],
    "daily_activity": [{"day": "2026-07-01", "score": None, "steps": None, "active_calories": None}],
    "daily_stress": [{"day": "2026-07-01", "stress_high": None, "recovery_high": None,
                      "day_summary": None}],
    "sleep": [{"day": "2026-07-01", "type": "long_sleep", "total_sleep_duration": None,
               "bedtime_start": None, "average_breath": None, "lowest_heart_rate": None,
               "average_hrv": None, "efficiency": None, "latency": None}],
    "daily_spo2": [{"day": "2026-07-01", "spo2_percentage": None,
                    "breathing_disturbance_index": None}],
    "daily_resilience": [{"day": "2026-07-01", "level": None, "contributors": None}],
    "daily_cardiovascular_age": [{"day": "2026-07-01", "pulse_wave_velocity": None,
                                  "vascular_age": None}],
    "vO2_max": [],
    "workout": [{"day": "2026-07-01", "activity": None, "intensity": None, "distance": None,
                 "calories": None, "source": None, "label": None,
                 "start_datetime": None, "end_datetime": None}],
    "sleep_time": [{"day": "2026-07-01", "optimal_bedtime": None, "recommendation": None,
                    "status": None}],
    "heartrate": [{"bpm": None, "source": None, "timestamp": None}],
}


@pytest.mark.parametrize("tool_name", [
    "oura_get_daily_summary", "oura_get_sleep_detail", "oura_get_readiness_detail",
    "oura_get_stress_resilience", "oura_get_workouts", "oura_get_baselines",
    "oura_get_sleep_time",
])
def test_nulls_never_render_as_literal_none(monkeypatch, tool_name):
    """Oura sends explicit JSON nulls; dict.get(k, "") does NOT catch them."""
    monkeypatch.setattr(server, "_fetch", _fake_fetch(NULL_SAMPLE))
    out = run(getattr(server, tool_name)())
    assert "None" not in out, f"{tool_name} leaked a literal None:\n{out}"


# --------------------------------------------------------------------------
# 0.1.1 regression: page-cap truncation must be surfaced, not silent
# --------------------------------------------------------------------------


def test_truncation_is_surfaced(monkeypatch):
    monkeypatch.setattr(server, "_fetch", _fake_fetch(NULL_SAMPLE, truncated=True))
    out = run(server.oura_get_daily_summary())
    assert "truncated" in out, "silent data loss: truncation note missing"


def test_no_truncation_note_when_complete(monkeypatch):
    monkeypatch.setattr(server, "_fetch", _fake_fetch(NULL_SAMPLE, truncated=False))
    out = run(server.oura_get_daily_summary())
    assert "truncated" not in out


# --------------------------------------------------------------------------
# 0.2.0: high-value fields must stay in the join table / baselines
# --------------------------------------------------------------------------


def test_daily_summary_surfaces_leading_indicators(monkeypatch):
    sample = dict(NULL_SAMPLE)
    sample["sleep"] = [{"day": "2026-07-01", "type": "long_sleep",
                        "total_sleep_duration": 25200, "bedtime_start": "2026-07-01T01:24:00-04:00",
                        "average_breath": 17.5, "lowest_heart_rate": 55, "average_hrv": 42}]
    sample["daily_spo2"] = [{"day": "2026-07-01", "spo2_percentage": {"average": 95.2},
                             "breathing_disturbance_index": 7}]
    monkeypatch.setattr(server, "_fetch", _fake_fetch(sample))
    out = run(server.oura_get_daily_summary())
    header, row = out.splitlines()[0], out.splitlines()[1]
    for col in ("bedtime", "resp_rate_brpm", "breathing_disturbance_idx"):
        assert col in header, f"{col} dropped from daily_summary"
    assert "01:24" in row      # bedtime decoded from the timestamp
    assert "17.5" in row       # respiratory rate
    assert ",7," in row        # breathing disturbance index


def test_baselines_surfaces_pulse_wave_velocity(monkeypatch):
    sample = dict(NULL_SAMPLE)
    sample["daily_cardiovascular_age"] = [
        {"day": "2026-07-01", "pulse_wave_velocity": 7.417530536651611, "vascular_age": 44}]
    monkeypatch.setattr(server, "_fetch", _fake_fetch(sample))
    out = run(server.oura_get_baselines())
    assert "pulse_wave_velocity_ms" in out.splitlines()[0]
    assert "7.42" in out, "PWV should be rounded, not raw float noise"


def test_baselines_vo2_sparsity_footer(monkeypatch):
    """vo2_max is a sparse EVENT; blanks must not read as 'no data'."""
    sample = dict(NULL_SAMPLE)
    sample["vO2_max"] = [{"day": "2026-04-30", "vo2_max": 40}]
    monkeypatch.setattr(server, "_fetch", _fake_fetch(sample))
    out = run(server.oura_get_baselines())
    assert "latest = 40 on 2026-04-30" in out

    sample["vO2_max"] = []
    monkeypatch.setattr(server, "_fetch", _fake_fetch(sample))
    out = run(server.oura_get_baselines())
    assert "no reading in this window" in out


# --------------------------------------------------------------------------
# 0.2.1: sleep_time offset decoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seconds,expected", [
    (1800, "00:30"),      # 30 min past midnight
    (4500, "01:15"),
    (0, "00:00"),
    (-3600, "23:00"),     # before midnight must wrap, not go negative
    (None, ""),
])
def test_offset_to_hhmm(seconds, expected):
    assert server._offset_to_hhmm(seconds) == expected


def test_sleep_time_decodes_window_and_flags_streak(monkeypatch):
    sample = dict(NULL_SAMPLE)
    sample["sleep_time"] = [
        {"day": "2026-06-15", "status": "optimal_found", "recommendation": "later_wake_up_time",
         "optimal_bedtime": {"day_tz": -14400, "start_offset": 1800, "end_offset": 4500}},
        {"day": "2026-06-24", "status": "only_recommended_found",
         "recommendation": "earlier_bedtime", "optimal_bedtime": None},
        {"day": "2026-06-25", "status": "only_recommended_found",
         "recommendation": "earlier_bedtime", "optimal_bedtime": None},
        {"day": "2026-06-26", "status": "only_recommended_found",
         "recommendation": "earlier_bedtime", "optimal_bedtime": None},
    ]
    monkeypatch.setattr(server, "_fetch", _fake_fetch(sample))
    out = run(server.oura_get_sleep_time())
    assert "00:30,01:15" in out, "optimal bedtime offsets not decoded to HH:MM"
    assert "trend: 'earlier_bedtime' for the last 3" in out, "persistent rec not surfaced"


# --------------------------------------------------------------------------
# input validation / errors are actionable
# --------------------------------------------------------------------------


def test_inverted_date_range_raises_actionable_error():
    with pytest.raises(RuntimeError, match="after end_date"):
        server._resolve_dates("2026-07-10", "2026-07-01", 30)


def test_bad_date_format_raises_actionable_error():
    with pytest.raises(RuntimeError, match="ISO format"):
        server._resolve_dates("last tuesday", None, 30)


def test_missing_token_error_names_the_env_var(monkeypatch):
    monkeypatch.delenv("OURA_PERSONAL_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="OURA_PERSONAL_ACCESS_TOKEN"):
        server._client()


def test_main_sleep_prefers_long_sleep_over_longer_nap():
    """A 'sleep' (nap) must not displace the night's long_sleep even if oddly long."""
    periods = [
        {"day": "2026-07-01", "type": "sleep", "total_sleep_duration": 9999},
        {"day": "2026-07-01", "type": "long_sleep", "total_sleep_duration": 100},
    ]
    main = server._main_sleep_by_day(periods)
    assert main["2026-07-01"]["type"] == "long_sleep"
