# Oura MCP server

A Model Context Protocol (MCP) server that gives Claude **read-only** access to your
[Oura ring](https://ouraring.com) biometrics via the [Oura API v2](https://cloud.ouraring.com/v2/docs).

It's designed to sit **alongside a Strava connector**: every tool is namespaced `oura_*` and
keyed by ISO date (`YYYY-MM-DD`), so Claude can join Oura recovery data against Strava training
load on `date` and answer questions like *"how did last week's hard sessions affect my sleep,
stress, and next-day readiness?"* in one reasoning step.

## Tools

| Tool | What it returns | Default window |
|------|-----------------|----------------|
| `oura_get_daily_summary` | **Start here.** One row/day: readiness, sleep & activity scores, total sleep, resting HR, HRV, temp deviation, steps, active calories, daytime stress. The cross-source join table. | 30 days |
| `oura_get_sleep_detail` | Per-night architecture: bedtime, sleep stages (deep/REM/light), efficiency, latency, avg/lowest HR, HRV, respiratory rate. | 14 days |
| `oura_get_readiness_detail` | Readiness with every contributor (hrv_balance, resting HR, recovery index, body temp, previous-day activity, sleep balance…) — explains *why* readiness moved. | 30 days |
| `oura_get_stress_resilience` | Daytime stress vs. recovery minutes, day summary, and long-term resilience level + contributors. | 30 days |
| `oura_get_workouts` | Workouts as logged by Oura — reconcile against Strava, catch missed sessions, see intensity labels. | 30 days |
| `oura_get_baselines` | Slow-moving baselines: overnight SpO2, breathing disturbance, cardiovascular/vascular age, VO2 max. | 90 days |
| `oura_get_heart_rate` | Fine-grained HR timeseries, tagged by source (awake/rest/sleep/workout). Aggregated stats by default. | 24 hours |

All output is compact CSV with units stated in the column names.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

> **New here?** The [full setup & configuration guide](docs/SETUP.md) walks through install,
> token creation, the Claude Desktop config, common mistakes, and troubleshooting.

## Authentication

This server uses an Oura **Personal Access Token** (PAT):

1. Go to <https://cloud.ouraring.com/personal-access-tokens> and create a token.
2. Copy `.env.example` to `.env` and paste the token into `OURA_PERSONAL_ACCESS_TOKEN`
   (for local dev), or set it in the Claude Desktop `env` block below.

Never commit your real token — `.env` is gitignored.

## Run / test

```bash
fastmcp dev server.py     # opens the MCP Inspector to exercise each tool manually
```

## Install in Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
Windows: `%APPDATA%\Claude\`) and add:

```json
{
  "mcpServers": {
    "oura": {
      "command": "/Users/ericcarr/Documents/GitHub/oura-mcp-server/.venv/bin/python",
      "args": ["/Users/ericcarr/Documents/GitHub/oura-mcp-server/server.py"],
      "env": {
        "OURA_PERSONAL_ACCESS_TOKEN": "your-token-here"
      }
    }
  }
}
```

Use the venv's Python interpreter and an absolute path to `server.py`. Fully quit and reopen
Claude Desktop after editing. The `oura_*` tools then appear in the tools/connectors menu,
ready to use alongside your Strava tools.

## Notes & limits

- **Read-only.** No tool writes to your Oura account.
- Dates are ISO `YYYY-MM-DD` and the range is inclusive. Heart rate uses ISO 8601 datetimes.
- Some metrics are sparse or ring/firmware-dependent (VO2 max, cardiovascular age, resilience
  need enough history); rows show blanks where a metric wasn't measured.
- Oura's rate limit is ~5000 requests/day; tools default to modest windows and paginate
  automatically.
