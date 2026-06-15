# Setup & configuration guide

Step-by-step setup for the Oura MCP server, including the gotchas that are easy to miss.

## 1. Install

```bash
git clone https://github.com/<you>/oura-mcp-server.git
cd oura-mcp-server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

This installs `fastmcp` and `httpx` into the virtualenv and makes `server.py` runnable.

## 2. Get an Oura Personal Access Token (PAT)

1. Sign in at <https://cloud.ouraring.com/personal-access-tokens>.
2. Click **Create New Personal Access Token**, give it a name (e.g. "Claude MCP"), and copy it.
3. Treat it like a password — it grants read access to your biometrics. You can revoke and
   regenerate it from that same page at any time.

## 3. Configure Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add an `oura` server block (see [`claude_desktop_config.example.json`](../claude_desktop_config.example.json)):

```json
{
  "mcpServers": {
    "oura": {
      "command": "/ABSOLUTE/PATH/TO/oura-mcp-server/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/oura-mcp-server/server.py"],
      "env": {
        "OURA_PERSONAL_ACCESS_TOKEN": "your-token-here"
      }
    }
  }
}
```

### ⚠️ Common mistakes

- **Token in the wrong place.** It must be the **value** of `OURA_PERSONAL_ACCESS_TOKEN`, not the
  key. A block like `"env": { "BBUR...token...": "" }` is wrong — the server will report
  `OURA_PERSONAL_ACCESS_TOKEN is not set`. Correct form:
  `"env": { "OURA_PERSONAL_ACCESS_TOKEN": "BBUR...token..." }`.
- **Relative paths.** `command` and `args` must be **absolute** paths. Point `command` at the
  venv's Python so dependencies resolve.
- **Forgetting to restart.** Claude Desktop only reads the config and launches the server on
  startup. After editing, **fully quit** (Cmd+Q on macOS — closing the window isn't enough) and
  reopen. Changing the token later also requires a full restart.

## 4. Verify

After restarting, the `oura_*` tools appear in Claude Desktop's tools menu. Ask:

> "Pull my Oura daily summary for the last 7 days."

To test the server independently of Claude Desktop, run the MCP Inspector:

```bash
OURA_PERSONAL_ACCESS_TOKEN=your-token fastmcp dev server.py
```

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `OURA_PERSONAL_ACCESS_TOKEN is not set` | Token missing or set as the key instead of the value; or Claude Desktop wasn't restarted after editing config. |
| `... is invalid or expired` (401) | Regenerate the PAT at cloud.ouraring.com and update the config. |
| `403` on a tool | Your ring/firmware/subscription may not produce that data type (e.g. VO2 max, cardiovascular age). |
| Recent days show blank readiness/sleep | Normal sync lag — Oura processes the night after the ring syncs. |
| `429` rate limit | Wait and retry, or request a smaller date range (limit ~5000 req/day). |

## Using it with Strava

This server is designed to sit beside a Strava MCP connector. Both expose date-keyed tools
(`oura_*` / `strava_*`), so Claude can join them on `date` — e.g. *"compare last month's Strava
training load against my Oura readiness and sleep, and flag days I overreached."* Keep date
formats ISO (`YYYY-MM-DD`) across both servers so the join stays clean.
