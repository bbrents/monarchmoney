# Monarch Money MCP server

Read-only MCP server exposing your Monarch Money account to Claude Desktop.

## One-time setup

Already done on this machine:

- Virtualenv at `.venv` with `monarchmoneycommunity` (editable) + `mcp` 2.2.0
- Registered in Claude Desktop's config as `monarch-money`

Remaining step — create the saved session (needs your password, so it can't be
automated):

```powershell
cd c:\Users\Progn\projects\monarch_api
.venv\Scripts\python.exe mcp_server\login.py
```

Then fully quit and reopen Claude Desktop (tray icon → Quit; closing the window
is not enough).

## Why a session file

MCP servers run non-interactively, so they can't prompt for an MFA code.
`login.py` does the interactive login once and saves a long-lived session to
`.mm/mm_session.pickle` (git-ignored). The server reads it on startup and needs
no credentials in the Claude Desktop config.

If you'd rather not keep a session file, set `MONARCH_EMAIL`, `MONARCH_PASSWORD`,
and `MONARCH_MFA_SECRET` (the TOTP secret, not a 6-digit code) in the `env`
block of the server entry instead.

## Tools

| Tool | Purpose |
| --- | --- |
| `monarch_auth_status` | Diagnose the connection; run this first if something fails |
| `list_accounts` | Accounts, balances, institutions, and account IDs |
| `account_balances` | Daily balance history for net-worth trends |
| `list_transactions` | Search/filter transactions by date, account, category, tag |
| `transaction_categories` | Categories and groups with IDs |
| `budgets` | Budget targets vs. actual spend |
| `cashflow_summary` | Income, expense, savings, savings rate for a period |
| `cashflow_by_category` | Spending broken down by category and merchant |
| `recurring_transactions` | Upcoming subscriptions and bills |
| `investment_holdings` | Holdings across brokerage accounts |
| `find_duplicate_transactions` | Likely duplicate charges |

All tools are read-only; nothing here can modify your Monarch data.

## Configuration

Environment variables (all optional, set in the Claude Desktop config `env` block):

| Variable | Default |
| --- | --- |
| `MONARCH_SESSION_FILE` | `<repo>/.mm/mm_session.pickle` |
| `MONARCH_EMAIL` / `MONARCH_PASSWORD` | unset (session file preferred) |
| `MONARCH_MFA_SECRET` | unset |
| `MONARCH_MAX_RESPONSE_CHARS` | `60000` |

Responses are stripped of GraphQL `__typename` noise and nulls, then capped at
`MONARCH_MAX_RESPONSE_CHARS` so a broad query can't flood the context window.

## Config location on this machine

Claude Desktop is installed as an MSIX package, so its config is **filesystem
virtualized**. The real file is:

```
C:\Users\Progn\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

`%APPDATA%\Claude\claude_desktop_config.json` does **not** exist — edits there
are ignored by the app.

## Troubleshooting

- **Tools missing in Claude Desktop** — fully quit the app from the tray and
  relaunch; it only reads the config at startup.
- **Auth errors** — the session expired. Re-run `login.py`.
- **Server logs** — `%LOCALAPPDATA%\Claude\logs\mcp.log`.
