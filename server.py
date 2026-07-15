"""oura — MCP server for Oura ring biometrics (FastMCP).

Read-only access to the Oura API v2 (https://api.ouraring.com/v2/usercollection),
shaped for combining with a Strava connector to analyze how activity and training
load affect sleep, stress, recovery, and next-day readiness.

Design notes:
  - Tools are namespaced `oura_*` and date-keyed (ISO YYYY-MM-DD) so their output
    joins cleanly, on `date`, against a sibling `strava_*` connector.
  - `oura_get_daily_summary` is the workhorse: one tidy row per day joining
    readiness + sleep + activity + recovery signals, so the agent answers
    "how did training affect recovery?" without chaining many calls.
  - Output is compact CSV with units stated in the header/docstring.
  - Auth is a Personal Access Token (cloud.ouraring.com/personal-access-tokens),
    read from the environment, sent as a Bearer token.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field
from fastmcp import FastMCP

mcp = FastMCP("oura")

API_BASE = "https://api.ouraring.com/v2/usercollection"
TOKEN_ENV = "OURA_PERSONAL_ACCESS_TOKEN"

# ---------------------------------------------------------------------------
# HTTP + shared helpers
# ---------------------------------------------------------------------------


def _client() -> httpx.AsyncClient:
    """Build an authenticated async client. Token comes from the environment."""
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV} is not set. Create a Personal Access Token at "
            "https://cloud.ouraring.com/personal-access-tokens and add it to your .env "
            "(local dev) or the 'env' block of this server in claude_desktop_config.json."
        )
    return httpx.AsyncClient(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


def _raise_for_status(resp: httpx.Response, path: str) -> None:
    """Convert HTTP failures into actionable guidance instead of raw tracebacks."""
    if resp.status_code == 401:
        raise RuntimeError(
            f"{TOKEN_ENV} is invalid or expired. Generate a new Personal Access Token at "
            "https://cloud.ouraring.com/personal-access-tokens and update your config."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            f"Access to {path} was forbidden. Your token may lack the required scope, or this "
            "data type isn't available for your ring/subscription."
        )
    if resp.status_code == 422:
        raise RuntimeError(
            f"Invalid request to {path}: {resp.text}. Dates must be ISO (YYYY-MM-DD); "
            "datetimes must be ISO 8601 (e.g. 2026-06-14T00:00:00)."
        )
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "a few minutes")
        raise RuntimeError(
            f"Oura rate limit reached. Wait {retry} and retry, or request a smaller date range. "
            "(Limit is ~5000 requests/day.)"
        )
    resp.raise_for_status()


async def _fetch(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_pages: int = 50,
) -> tuple[list[dict], bool]:
    """GET a usercollection endpoint, following `next_token` pagination.

    Returns (records, truncated). `truncated` is True when the internal
    `max_pages` cap stopped pagination with more data still available —
    callers must surface that to the agent rather than dropping it silently.
    """
    params = dict(params or {})
    out: list[dict] = []
    token: str | None = None
    for _ in range(max_pages):
        resp = await client.get(f"/{path}", params=params)
        _raise_for_status(resp, path)
        payload = resp.json()
        out.extend(payload.get("data", []))
        token = payload.get("next_token")
        if not token:
            break
        params["next_token"] = token
    return out, bool(token)


_TRUNC_NOTE = "\n# note: pagination was truncated by the server's page cap; narrow the date range for complete data."


def _resolve_dates(start_date: str | None, end_date: str | None, default_days: int) -> tuple[str, str]:
    """Fill in a sensible default window: the last `default_days` ending today.

    Validates ISO format and that start <= end. Oura treats the range as inclusive.
    """
    today = date.today()
    end = _parse_date(end_date) if end_date else today
    start = _parse_date(start_date) if start_date else end - timedelta(days=default_days)
    if start > end:
        raise RuntimeError(
            f"start_date ({start.isoformat()}) is after end_date ({end.isoformat()}). Swap them."
        )
    return start.isoformat(), end.isoformat()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise RuntimeError(f"Invalid date '{value}'. Use ISO format, e.g. 2026-06-01.")


def _s(value: Any) -> Any:
    """Blank for None. Oura sends explicit JSON nulls (e.g. day_summary: null),
    which dict.get(key, "") does NOT catch — the key exists, so the default is
    ignored and the null would render as a literal 'None' in CSV output."""
    return "" if value is None else value


def _h(seconds: Any) -> str:
    """Seconds -> hours, 2dp. Blank if missing."""
    if seconds in (None, ""):
        return ""
    return f"{float(seconds) / 3600:.2f}"


def _min(seconds: Any) -> str:
    """Seconds -> whole minutes. Blank if missing."""
    if seconds in (None, ""):
        return ""
    return str(round(float(seconds) / 60))


def _g(d: dict, *keys: str, default: Any = "") -> Any:
    """Nested get: _g(rec, 'spo2_percentage', 'average'). None-safe."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur if cur is not None else default


def _by_day(records: list[dict]) -> dict[str, dict]:
    """Index daily records by their `day` field."""
    return {r["day"]: r for r in records if r.get("day")}


def _main_sleep_by_day(periods: list[dict]) -> dict[str, dict]:
    """From per-period sleep records, pick the main nightly sleep for each day.

    Prefers type == 'long_sleep'; otherwise the longest period that day.
    """
    out: dict[str, dict] = {}
    for p in periods:
        day = p.get("day")
        if not day:
            continue
        cur = out.get(day)
        if cur is None:
            out[day] = p
            continue
        # Prefer a long_sleep; else the longer total_sleep_duration.
        if p.get("type") == "long_sleep" and cur.get("type") != "long_sleep":
            out[day] = p
        elif (p.get("total_sleep_duration") or 0) > (cur.get("total_sleep_duration") or 0) and cur.get("type") != "long_sleep":
            out[day] = p
    return out


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_daily_summary(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 30 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
) -> str:
    """One row per day joining readiness, sleep, activity, and recovery signals.

    THIS IS THE CROSS-SOURCE JOIN TABLE. Use it first for almost any question about
    how training/activity relates to recovery — it lets you join Oura against a Strava
    connector on `date` in one step. Reach for the detail tools only to drill into a
    specific signal.

    CSV columns (units in the name):
      date, readiness_score(0-100), sleep_score(0-100), activity_score(0-100),
      total_sleep_h, bedtime (HH:MM local, when sleep started),
      resting_hr_bpm (lowest nightly HR, a resting-HR proxy), avg_hrv_ms,
      resp_rate_brpm (overnight respiratory rate), temp_deviation_c (body temp vs
      baseline), breathing_disturbance_idx, steps, active_cal,
      stress_high_min (stressful daytime minutes), day_summary

    Defaults to the last 30 days. Higher readiness/sleep/activity scores are better;
    a rising resting_hr or positive temp_deviation often signals incomplete recovery.
    resp_rate_brpm and breathing_disturbance_idx are leading illness/strain flags —
    a sustained rise in either often precedes a subjective sense of getting sick.
    bedtime is included because sleep *timing* (not just duration) drives next-day
    recovery, and it anchors any analysis of late meals/alcohol relative to sleep.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=30)
    params = {"start_date": s, "end_date": e}
    async with _client() as client:
        results = await asyncio.gather(
            _fetch(client, "daily_readiness", params),
            _fetch(client, "daily_sleep", params),
            _fetch(client, "daily_activity", params),
            _fetch(client, "daily_stress", params),
            _fetch(client, "sleep", params),
            _fetch(client, "daily_spo2", params),
        )
    truncated = any(t for _, t in results)
    readiness = _by_day(results[0][0])
    daily_sleep = _by_day(results[1][0])
    activity = _by_day(results[2][0])
    stress = _by_day(results[3][0])
    sleep = _main_sleep_by_day(results[4][0])
    spo2 = _by_day(results[5][0])

    days = sorted(set(readiness) | set(daily_sleep) | set(activity) | set(stress)
                  | set(sleep) | set(spo2))
    header = (
        "date,readiness_score,sleep_score,activity_score,total_sleep_h,bedtime,"
        "resting_hr_bpm,avg_hrv_ms,resp_rate_brpm,temp_deviation_c,"
        "breathing_disturbance_idx,steps,active_cal,stress_high_min,day_summary"
    )
    lines = [header]
    for d in days:
        r, ds, ac, st, sl, sp = (
            readiness.get(d, {}), daily_sleep.get(d, {}), activity.get(d, {}),
            stress.get(d, {}), sleep.get(d, {}), spo2.get(d, {}),
        )
        bedtime = (sl.get("bedtime_start") or "")[11:16]
        lines.append(
            f"{d},{_s(r.get('score'))},{_s(ds.get('score'))},{_s(ac.get('score'))},"
            f"{_h(sl.get('total_sleep_duration'))},{bedtime},"
            f"{_s(sl.get('lowest_heart_rate'))},{_s(sl.get('average_hrv'))},"
            f"{_s(sl.get('average_breath'))},{_s(r.get('temperature_deviation'))},"
            f"{_s(sp.get('breathing_disturbance_index'))},"
            f"{_s(ac.get('steps'))},{_s(ac.get('active_calories'))},"
            f"{_min(st.get('stress_high'))},{_s(st.get('day_summary'))}"
        )
    if len(lines) == 1:
        return f"No Oura data found for {s}..{e}. Check the date range or that your ring synced."
    return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_sleep_detail(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 14 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
    response_format: Annotated[
        Literal["concise", "detailed"],
        Field(description="'concise' = CSV of key metrics; 'detailed' = full JSON incl. period ids and timeseries."),
    ] = "concise",
) -> str:
    """Per-night sleep architecture for drilling into specific nights.

    Use after oura_get_daily_summary when you need stage breakdown or sleep timing.
    One row per sleep period (a day may have a nap plus the main sleep).

    Concise CSV columns:
      date, type, bedtime_start, bedtime_end, total_sleep_h, time_in_bed_h,
      deep_h, rem_h, light_h, awake_h, efficiency_pct, latency_min,
      avg_hr_bpm, lowest_hr_bpm, avg_hrv_ms, resp_rate_brpm

    Defaults to the last 14 days. Use 'detailed' for raw fields and 5-min hypnogram.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=14)
    async with _client() as client:
        periods, truncated = await _fetch(client, "sleep", {"start_date": s, "end_date": e})

    if not periods:
        return f"No sleep records for {s}..{e}."
    if response_format == "detailed":
        return json.dumps(periods, indent=2) + (_TRUNC_NOTE if truncated else "")

    header = (
        "date,type,bedtime_start,bedtime_end,total_sleep_h,time_in_bed_h,deep_h,rem_h,"
        "light_h,awake_h,efficiency_pct,latency_min,avg_hr_bpm,lowest_hr_bpm,avg_hrv_ms,resp_rate_brpm"
    )
    lines = [header]
    for p in sorted(periods, key=lambda x: (x.get("day", ""), x.get("bedtime_start", "") or "")):
        lines.append(
            f"{_s(p.get('day'))},{_s(p.get('type'))},"
            f"{(p.get('bedtime_start') or '')[11:16]},{(p.get('bedtime_end') or '')[11:16]},"
            f"{_h(p.get('total_sleep_duration'))},{_h(p.get('time_in_bed'))},"
            f"{_h(p.get('deep_sleep_duration'))},{_h(p.get('rem_sleep_duration'))},"
            f"{_h(p.get('light_sleep_duration'))},{_h(p.get('awake_time'))},"
            f"{_s(p.get('efficiency'))},{_min(p.get('latency'))},"
            f"{_s(p.get('average_heart_rate'))},{_s(p.get('lowest_heart_rate'))},"
            f"{_s(p.get('average_hrv'))},{_s(p.get('average_breath'))}"
        )
    return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_readiness_detail(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 30 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
) -> str:
    """Daily readiness with every contributor broken out — explains WHY readiness moved.

    Use when oura_get_daily_summary shows a readiness change and you want the driver.
    Each contributor is a 0-100 sub-score (higher = better). A low hrv_balance or
    resting_heart_rate contributor the morning after a hard session points to load.

    CSV columns:
      date, score, temp_deviation_c, activity_balance, body_temperature,
      hrv_balance, previous_day_activity, previous_night, recovery_index,
      resting_heart_rate, sleep_balance

    Defaults to the last 30 days.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=30)
    async with _client() as client:
        records, truncated = await _fetch(client, "daily_readiness", {"start_date": s, "end_date": e})

    if not records:
        return f"No readiness records for {s}..{e}."
    header = (
        "date,score,temp_deviation_c,activity_balance,body_temperature,hrv_balance,"
        "previous_day_activity,previous_night,recovery_index,resting_heart_rate,sleep_balance"
    )
    lines = [header]
    for r in sorted(records, key=lambda x: x.get("day", "")):
        c = r.get("contributors") or {}
        lines.append(
            f"{_s(r.get('day'))},{_s(r.get('score'))},{_s(r.get('temperature_deviation'))},"
            f"{_s(c.get('activity_balance'))},{_s(c.get('body_temperature'))},{_s(c.get('hrv_balance'))},"
            f"{_s(c.get('previous_day_activity'))},{_s(c.get('previous_night'))},"
            f"{_s(c.get('recovery_index'))},{_s(c.get('resting_heart_rate'))},{_s(c.get('sleep_balance'))}"
        )
    return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_stress_resilience(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 30 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
) -> str:
    """Daytime stress load plus long-term resilience — the daytime side of recovery.

    Combines the daily_stress and daily_resilience collections into one table so you
    can see whether high-load days accumulate physiological stress and erode resilience.

    CSV columns:
      date, stress_high_min (stressful daytime minutes), recovery_high_min (restorative
      minutes), day_summary (restored|normal|stressful), resilience_level
      (limited|adequate|solid|strong|exceptional), sleep_recovery, daytime_recovery, stress
      (the last three are 0-100 resilience contributors).

    Defaults to the last 30 days. Resilience needs ~weeks of data to populate.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=30)
    params = {"start_date": s, "end_date": e}
    async with _client() as client:
        (stress_recs, t1), (resilience_recs, t2) = await asyncio.gather(
            _fetch(client, "daily_stress", params),
            _fetch(client, "daily_resilience", params),
        )
    stress = _by_day(stress_recs)
    resilience = _by_day(resilience_recs)

    days = sorted(set(stress) | set(resilience))
    if not days:
        return f"No stress/resilience records for {s}..{e}."
    header = (
        "date,stress_high_min,recovery_high_min,day_summary,resilience_level,"
        "sleep_recovery,daytime_recovery,stress"
    )
    lines = [header]
    for d in days:
        st, rs = stress.get(d, {}), resilience.get(d, {})
        c = rs.get("contributors") or {}
        lines.append(
            f"{d},{_min(st.get('stress_high'))},{_min(st.get('recovery_high'))},"
            f"{_s(st.get('day_summary'))},{_s(rs.get('level'))},"
            f"{_s(c.get('sleep_recovery'))},{_s(c.get('daytime_recovery'))},{_s(c.get('stress'))}"
        )
    return "\n".join(lines) + (_TRUNC_NOTE if (t1 or t2) else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_workouts(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 30 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
) -> str:
    """Workouts as logged by Oura — cross-check or supplement Strava activities.

    Oura auto-detects and lets you tag workouts. Use this to reconcile against a Strava
    connector (match on date/time), catch sessions Strava missed, or see Oura's intensity
    label. `source` shows how the workout was recorded (e.g. auto_detected, manual, confirmed).

    CSV columns:
      date, activity, intensity (easy|moderate|hard), start_time, end_time,
      duration_min, distance_km, calories, source, label

    Defaults to the last 30 days.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=30)
    async with _client() as client:
        workouts, truncated = await _fetch(client, "workout", {"start_date": s, "end_date": e})

    if not workouts:
        return f"No Oura-logged workouts for {s}..{e}."
    header = "date,activity,intensity,start_time,end_time,duration_min,distance_km,calories,source,label"
    lines = [header]
    for w in sorted(workouts, key=lambda x: x.get("start_datetime", "") or ""):
        start, end_dt = w.get("start_datetime") or "", w.get("end_datetime") or ""
        dur = ""
        if start and end_dt:
            try:
                dur = str(round((datetime.fromisoformat(end_dt) - datetime.fromisoformat(start)).total_seconds() / 60))
            except ValueError:
                dur = ""
        dist = w.get("distance")
        dist_km = f"{dist / 1000:.2f}" if isinstance(dist, (int, float)) else ""
        lines.append(
            f"{_s(w.get('day'))},{_s(w.get('activity'))},{_s(w.get('intensity'))},"
            f"{start[11:16]},{end_dt[11:16]},{dur},{dist_km},"
            f"{_s(w.get('calories'))},{_s(w.get('source'))},{w.get('label') or ''}"
        )
    return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_baselines(
    start_date: Annotated[str | None, Field(description="ISO start date (YYYY-MM-DD). Defaults to 90 days before end_date.")] = None,
    end_date: Annotated[str | None, Field(description="ISO end date (YYYY-MM-DD), inclusive. Defaults to today.")] = None,
) -> str:
    """Slow-moving health baselines: SpO2, breathing, arterial stiffness, VO2 max.

    These trend over weeks/months, so the default window is 90 days. Use to track
    long-term vascular and aerobic health alongside training history.

    CSV columns:
      date, spo2_avg_pct (overnight blood-oxygen %), breathing_disturbance_index,
      pulse_wave_velocity_ms, vascular_age_years, vo2_max_ml_kg_min

    pulse_wave_velocity_ms (m/s) is the RAW arterial-stiffness measurement and the most
    clinically meaningful vascular metric here — lower is better/more elastic; it is the
    same class of measure (PWV) used in hypertension research. vascular_age_years is a
    derived presentation of it, so prefer PWV when tracking real change.

    IMPORTANT — sparsity: vo2_max is a measurement EVENT, not a daily value (often only
    a handful of readings per quarter), so most rows will be blank for it. Blank does NOT
    mean "no VO2 data" — read the footer, which reports the most recent reading in the
    window. cardiovascular_age/VO2 also require a compatible ring/firmware.
    """
    s, e = _resolve_dates(start_date, end_date, default_days=90)
    params = {"start_date": s, "end_date": e}
    async with _client() as client:
        (spo2_recs, t1), (cva_recs, t2), (vo2_recs, t3) = await asyncio.gather(
            _fetch(client, "daily_spo2", params),
            _fetch(client, "daily_cardiovascular_age", params),
            _fetch(client, "vO2_max", params),
        )
    spo2 = _by_day(spo2_recs)
    cva = _by_day(cva_recs)
    vo2 = _by_day(vo2_recs)

    days = sorted(set(spo2) | set(cva) | set(vo2))
    if not days:
        return f"No baseline records (SpO2/cardio-age/VO2max) for {s}..{e}."
    header = ("date,spo2_avg_pct,breathing_disturbance_index,pulse_wave_velocity_ms,"
              "vascular_age_years,vo2_max_ml_kg_min")
    lines = [header]
    for d in days:
        sp, cv, vo = spo2.get(d, {}), cva.get(d, {}), vo2.get(d, {})
        pwv = cv.get("pulse_wave_velocity")
        lines.append(
            f"{d},{_g(sp, 'spo2_percentage', 'average')},{_s(sp.get('breathing_disturbance_index'))},"
            f"{round(pwv, 2) if isinstance(pwv, (int, float)) else ''},"
            f"{_s(cv.get('vascular_age'))},{_s(vo.get('vo2_max'))}"
        )

    # vo2_max is sparse; surface the latest reading so blanks aren't misread as "no data".
    vo2_days = sorted(d for d, v in vo2.items() if v.get("vo2_max") is not None)
    if vo2_days:
        last = vo2_days[-1]
        lines.append(f"# vo2_max is measured infrequently: {len(vo2_days)} reading(s) in this window; "
                     f"latest = {vo2[last]['vo2_max']} on {last}. Blank rows mean 'not measured that day'.")
    else:
        lines.append("# vo2_max: no reading in this window (it is measured only every few weeks). "
                     "Widen start_date to find the most recent value before assuming none exists.")
    return "\n".join(lines) + (_TRUNC_NOTE if (t1 or t2 or t3) else "")


@mcp.tool(annotations={"readOnlyHint": True})
async def oura_get_heart_rate(
    start_datetime: Annotated[str | None, Field(description="ISO 8601 start, e.g. 2026-06-14T00:00:00. Defaults to 24h before end.")] = None,
    end_datetime: Annotated[str | None, Field(description="ISO 8601 end, e.g. 2026-06-15T00:00:00. Defaults to now.")] = None,
    response_format: Annotated[
        Literal["summary", "raw"],
        Field(description="'summary' = aggregated stats by source (few tokens); 'raw' = capped sample list."),
    ] = "summary",
    limit: Annotated[int, Field(ge=1, le=2000, description="Max raw samples to return when response_format='raw'.")] = 500,
) -> str:
    """Fine-grained heart-rate timeseries (one sample every few minutes).

    This is high-volume data, so default to a SHORT window (<= ~2 days) and the 'summary'
    format. Oura tags each sample with a `source`: awake, rest, sleep, workout, etc. —
    useful for isolating workout HR or overnight resting HR around hard training days.

    summary -> CSV with one row per source: source, samples, min_bpm, avg_bpm, max_bpm,
               plus a final 'all' row.
    raw     -> CSV: timestamp, bpm, source (capped at `limit`; narrow the window for more).
    """
    # timezone-aware defaults: Oura timestamps carry offsets, and a naive local
    # "now" can silently shift the window for late-night queries.
    end = datetime.fromisoformat(end_datetime) if end_datetime else datetime.now().astimezone()
    start = datetime.fromisoformat(start_datetime) if start_datetime else end - timedelta(days=1)
    if (start.tzinfo is None) != (end.tzinfo is None):
        # normalize mixed naive/aware inputs so comparison below can't crash
        if start.tzinfo is None:
            start = start.replace(tzinfo=end.tzinfo)
        else:
            end = end.replace(tzinfo=start.tzinfo)
    if start > end:
        raise RuntimeError("start_datetime is after end_datetime. Swap them.")
    params = {"start_datetime": start.isoformat(), "end_datetime": end.isoformat()}
    async with _client() as client:
        samples, truncated = await _fetch(client, "heartrate", params)

    if not samples:
        return f"No heart-rate samples for {start.isoformat()}..{end.isoformat()}."

    if response_format == "summary":
        buckets: dict[str, list[int]] = {}
        for smp in samples:
            bpm = smp.get("bpm")
            if bpm is None:
                continue
            buckets.setdefault(smp.get("source") or "unknown", []).append(bpm)
        lines = ["source,samples,min_bpm,avg_bpm,max_bpm"]
        all_bpm: list[int] = []
        for src in sorted(buckets):
            v = buckets[src]
            all_bpm.extend(v)
            lines.append(f"{src},{len(v)},{min(v)},{round(sum(v)/len(v))},{max(v)}")
        if all_bpm:
            lines.append(f"all,{len(all_bpm)},{min(all_bpm)},{round(sum(all_bpm)/len(all_bpm))},{max(all_bpm)}")
        return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")

    total = len(samples)
    rows = samples[:limit]
    lines = ["timestamp,bpm,source"]
    for smp in rows:
        lines.append(f"{_s(smp.get('timestamp'))},{_s(smp.get('bpm'))},{_s(smp.get('source'))}")
    if total > limit:
        lines.append(f"# showing first {limit} of {total} samples; narrow the time window for full coverage.")
    return "\n".join(lines) + (_TRUNC_NOTE if truncated else "")


if __name__ == "__main__":
    mcp.run()
