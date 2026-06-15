# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/echocharlie/oura-mcp-server/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/echocharlie/oura-mcp-server/releases/tag/v0.1.0
