# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-07-14

Hardening release: closes the last endpoint gap and adds the test/CI safety net the
project had been missing since 0.1.0.

### Added

- **`oura_get_sleep_time`** — Oura's own bedtime guidance (optimal bedtime window +
  recommendation), the last documented endpoint with data that wasn't exposed. Distinct
  from `daily_summary`'s `bedtime`, which reports when you *actually* slept; this reports
  what Oura thinks you *should* do. The API returns the window as raw second-offsets from
  local midnight (unusable as-is), so the tool decodes them to local `HH:MM` and wraps
  negative offsets correctly (`-3600` → `23:00`, not a negative hour). When a
  recommendation repeats for 3+ consecutive records the tool appends a trend footer, since
  a persistent recommendation means something a single row does not.
- **`tests/`** — 23 offline regression tests (network is monkeypatched; no token needed).
  They cover the bugs this project has actually shipped: literal-`None` leakage, silent
  pagination truncation, PWV/VO2 coverage, sleep_time offset decoding, actionable errors,
  and the nap-vs-long_sleep selection rule. Verified by mutation testing — reintroducing
  each historical bug makes the corresponding test fail.
- **GitHub Actions CI** — `compileall` + full test suite on Python 3.10 and 3.12.
- `dev` extra (`pip install -e ".[dev]"`) and pytest config in `pyproject.toml`.

## [0.2.0] - 2026-07-14

Metric-coverage release. An audit against the live API found several high-value fields
present in endpoints the server already called but never surfaced.

### Added

- `oura_get_baselines` now reports **`pulse_wave_velocity_ms`** — the raw arterial-stiffness
  measurement from `daily_cardiovascular_age`. Previously only the derived `vascular_age` was
  exposed, which meant the server surfaced a cooked presentation while hiding the underlying
  measurement. PWV is the clinically meaningful vascular metric of the two.
- `oura_get_daily_summary` now includes **`bedtime`**, **`resp_rate_brpm`**, and
  **`breathing_disturbance_idx`**. Respiratory rate and breathing disturbance are leading
  illness/strain indicators; bedtime anchors sleep-timing analysis. All three were already
  available in endpoints the tool fetched (`sleep`, `daily_spo2`) but were dropped on the floor.

### Fixed

- `vo2_max` blanks were misleading. VO2 max is a sparse measurement *event* (roughly a handful
  of readings per quarter), not a daily value, so most rows are legitimately blank — but an agent
  reading the CSV could reasonably conclude no VO2 data existed at all. `oura_get_baselines` now
  appends a footer reporting the count and the most recent reading in the window (or, if none,
  says so explicitly and suggests widening `start_date`). No endpoint bug: `vO2_max` was and
  remains the correct path; every other casing 404s.

## [0.1.1] - 2026-07-02

### Fixed

- Explicit JSON nulls from the Oura API (e.g. `day_summary: null`) rendered as a literal
  `None` in CSV output; all nullable fields now render blank.
- Pagination hitting the internal 50-page cap silently dropped remaining data; tools now
  append an explicit truncation note telling the agent to narrow the date range.

### Changed

- Multi-collection tools (`daily_summary`, `stress_resilience`, `baselines`) fetch their
  collections concurrently instead of sequentially (~5x faster daily summary).
- Heart-rate default time window is now timezone-aware; mixed naive/aware inputs are
  normalized instead of raising.
- Verified `daily_cardiovascular_age` and `vO2_max` endpoint paths against the live API.

## [0.1.0] - 2026-06-15

Initial release. A read-only [FastMCP](https://github.com/jlowin/fastmcp) server over the
[Oura API v2](https://cloud.ouraring.com/v2/docs), designed to compose with a Strava connector
for training and recovery analysis.

### Added

- **Seven `oura_*` tools**, all read-only and date-keyed (ISO `YYYY-MM-DD`) for clean joins
  against a sibling Strava connector:
  - `oura_get_daily_summary` — the cross-source join table: one row/day of readiness, sleep &
    activity scores, total sleep, resting HR, HRV, temperature deviation, steps, active calories,
    and daytime stress.
  - `oura_get_sleep_detail` — per-night sleep architecture (stages, efficiency, latency, HR, HRV,
    respiratory rate); `concise`/`detailed` output.
  - `oura_get_readiness_detail` — readiness with every contributor broken out.
  - `oura_get_stress_resilience` — daytime stress vs. recovery minutes plus long-term resilience.
  - `oura_get_workouts` — Oura-logged workouts for reconciling against Strava.
  - `oura_get_baselines` — slow-moving baselines: SpO2, breathing disturbance, cardiovascular age,
    VO2 max.
  - `oura_get_heart_rate` — fine-grained HR timeseries by source; aggregated summary by default.
- Personal Access Token authentication via the `OURA_PERSONAL_ACCESS_TOKEN` environment variable.
- Compact CSV output with units in the column names, sensible default date windows, automatic
  `next_token` pagination, and actionable error messages for 401/403/422/429.
- Documentation: README with tool reference and an Oura + Strava usage/examples section,
  `docs/SETUP.md` setup & troubleshooting guide, and `claude_desktop_config.example.json`.

[Unreleased]: https://github.com/echocharlie/oura-mcp-server/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/echocharlie/oura-mcp-server/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/echocharlie/oura-mcp-server/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/echocharlie/oura-mcp-server/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/echocharlie/oura-mcp-server/releases/tag/v0.1.0
