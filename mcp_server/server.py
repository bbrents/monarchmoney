"""Monarch Money MCP server (stdio) for Claude Desktop.

Exposes read-only Monarch Money data as MCP tools. Authentication resolves in
this order:

1. A saved session pickle (created once by ``login.py``) -- preferred.
2. ``MONARCH_EMAIL`` / ``MONARCH_PASSWORD`` (+ ``MONARCH_MFA_SECRET`` for TOTP).

Environment variables:
    MONARCH_SESSION_FILE       Absolute path to the session pickle.
    MONARCH_EMAIL              Account email (fallback login).
    MONARCH_PASSWORD           Account password (fallback login).
    MONARCH_MFA_SECRET         TOTP secret key, for non-interactive MFA.
    MONARCH_MAX_RESPONSE_CHARS Response size cap, default 60000.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from monarchmoney import LoginFailedException, MonarchMoney, RequireMFAException

# The library's own default session path is relative (".mm/mm_session.pickle"),
# which breaks under Claude Desktop because the launch CWD is unpredictable.
# Always resolve to an absolute path anchored at the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE") or str(
    _REPO_ROOT / ".mm" / "mm_session.pickle"
)
MAX_RESPONSE_CHARS = int(os.environ.get("MONARCH_MAX_RESPONSE_CHARS", "60000"))

mcp = MCPServer(
    name="monarch-money",
    instructions=(
        "Read-only access to the user's Monarch Money personal finance account: "
        "accounts and balances, transactions, budgets, cash flow, recurring "
        "charges, and investment holdings. Dates are 'YYYY-MM-DD'. Amounts are "
        "signed: negative is money out, positive is money in. Prefer narrow date "
        "ranges and filters -- results are capped and will be truncated."
    ),
)

_client: Optional[MonarchMoney] = None
_client_lock = asyncio.Lock()


async def _get_client() -> MonarchMoney:
    """Return a logged-in client, authenticating once and reusing it after."""
    global _client
    if _client is not None:
        return _client

    async with _client_lock:
        if _client is not None:  # another task won the race
            return _client

        mm = MonarchMoney(session_file=SESSION_FILE)

        if os.path.exists(SESSION_FILE):
            mm.load_session(SESSION_FILE)
            _client = mm
            return _client

        email = os.environ.get("MONARCH_EMAIL")
        password = os.environ.get("MONARCH_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                f"Not authenticated. No session file at {SESSION_FILE} and "
                "MONARCH_EMAIL/MONARCH_PASSWORD are not set. Run "
                "'python mcp_server/login.py' once to create a session."
            )

        try:
            await mm.login(
                email=email,
                password=password,
                use_saved_session=False,
                save_session=True,
                mfa_secret_key=os.environ.get("MONARCH_MFA_SECRET"),
            )
        except RequireMFAException as exc:
            raise RuntimeError(
                "Monarch requires multi-factor authentication. Set "
                "MONARCH_MFA_SECRET, or run 'python mcp_server/login.py' once "
                "to create a saved session."
            ) from exc
        except LoginFailedException as exc:
            raise RuntimeError(f"Monarch login failed: {exc}") from exc

        _client = mm
        return _client


def _strip(obj: Any) -> Any:
    """Drop GraphQL __typename noise and null fields to save context."""
    if isinstance(obj, dict):
        return {
            key: _strip(value)
            for key, value in obj.items()
            if key != "__typename" and value is not None
        }
    if isinstance(obj, list):
        return [_strip(item) for item in obj]
    return obj


def _dump(obj: Any) -> str:
    """Serialize a response compactly, capped so it cannot flood the context."""
    text = json.dumps(_strip(obj), separators=(",", ":"), default=str)
    if len(text) > MAX_RESPONSE_CHARS:
        return (
            text[:MAX_RESPONSE_CHARS]
            + f"\n\n[truncated at {MAX_RESPONSE_CHARS} chars -- narrow the date "
            "range, lower `limit`, or add filters]"
        )
    return text


def _default_range(start: Optional[str], end: Optional[str], days: int = 30):
    """Fill in a trailing-`days` window when the caller omits either bound."""
    today = date.today()
    return (
        start or (today - timedelta(days=days)).isoformat(),
        end or today.isoformat(),
    )


@mcp.tool()
async def monarch_auth_status() -> str:
    """Check whether the Monarch connection is working, and how it authenticated.

    Use this first when another tool reports an authentication problem.
    """
    have_session = os.path.exists(SESSION_FILE)
    try:
        client = await _get_client()
        accounts = await client.get_accounts()
        count = len(accounts.get("accounts", []))
        return _dump(
            {
                "authenticated": True,
                "method": "saved session" if have_session else "email/password",
                "session_file": SESSION_FILE,
                "accounts_visible": count,
            }
        )
    except Exception as exc:  # surfaced to the model as a diagnostic, not a crash
        return _dump(
            {
                "authenticated": False,
                "session_file": SESSION_FILE,
                "session_file_exists": have_session,
                "error": str(exc),
            }
        )


@mcp.tool()
async def list_accounts() -> str:
    """List all linked accounts with current balances, type, and institution.

    Start here to get account IDs for filtering transactions.
    """
    client = await _get_client()
    return _dump(await client.get_accounts())


@mcp.tool()
async def account_balances(start_date: Optional[str] = None) -> str:
    """Get daily balance history for every account, for net-worth trends.

    Args:
        start_date: ISO date (YYYY-MM-DD) to start from. Defaults to 31 days ago.
    """
    client = await _get_client()
    return _dump(await client.get_recent_account_balances(start_date=start_date))


@mcp.tool()
async def list_transactions(
    limit: int = 50,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: str = "",
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
    is_recurring: Optional[bool] = None,
    is_pending: Optional[bool] = None,
    needs_review: Optional[bool] = None,
) -> str:
    """Search and list transactions. Returns amount, date, merchant, and category.

    Amounts are signed: negative is spending, positive is income. Defaults to the
    last 30 days when no dates are given. Keep `limit` modest and prefer filters;
    large result sets get truncated.

    Args:
        limit: Max transactions to return (default 50).
        offset: Number to skip, for paging through `totalCount`.
        start_date: Earliest date, YYYY-MM-DD.
        end_date: Latest date, YYYY-MM-DD.
        search: Free-text filter on merchant/description.
        category_ids: Restrict to these category IDs.
        account_ids: Restrict to these account IDs.
        tag_ids: Restrict to these tag IDs.
        is_recurring: Only recurring (True) or only non-recurring (False).
        is_pending: Only pending (True) or only settled (False).
        needs_review: Only transactions flagged for review.
    """
    client = await _get_client()
    start_date, end_date = _default_range(start_date, end_date)
    return _dump(
        await client.get_transactions(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            search=search,
            category_ids=category_ids or [],
            account_ids=account_ids or [],
            tag_ids=tag_ids or [],
            is_recurring=is_recurring,
            is_pending=is_pending,
            needs_review=needs_review,
        )
    )


@mcp.tool()
async def transaction_categories() -> str:
    """List all spending/income categories and their groups, with IDs.

    Use this to resolve a category name to the ID that `list_transactions` wants.
    """
    client = await _get_client()
    return _dump(await client.get_transaction_categories())


@mcp.tool()
async def budgets(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> str:
    """Get budget targets alongside actual amounts spent per category.

    Args:
        start_date: Earliest month, YYYY-MM-DD. Defaults to last month.
        end_date: Latest month, YYYY-MM-DD. Defaults to next month.
    """
    client = await _get_client()
    return _dump(await client.get_budgets(start_date=start_date, end_date=end_date))


@mcp.tool()
async def cashflow_summary(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> str:
    """Get total income, total expense, savings, and savings rate for a period.

    This is the cheapest high-level "how am I doing" tool -- prefer it over
    pulling every transaction when the question is about totals.

    Args:
        start_date: Start of period, YYYY-MM-DD. Defaults to 30 days ago.
        end_date: End of period, YYYY-MM-DD. Defaults to today.
    """
    client = await _get_client()
    start_date, end_date = _default_range(start_date, end_date)
    return _dump(
        await client.get_cashflow_summary(start_date=start_date, end_date=end_date)
    )


@mcp.tool()
async def cashflow_by_category(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
) -> str:
    """Break spending and income down by category, category group, and merchant.

    Use this to answer "where did my money go" without listing transactions.

    Args:
        start_date: Start of period, YYYY-MM-DD. Defaults to 30 days ago.
        end_date: End of period, YYYY-MM-DD. Defaults to today.
        limit: Max aggregate rows (default 100).
    """
    client = await _get_client()
    start_date, end_date = _default_range(start_date, end_date)
    return _dump(
        await client.get_cashflow(
            limit=limit, start_date=start_date, end_date=end_date
        )
    )


@mcp.tool()
async def recurring_transactions(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> str:
    """List upcoming recurring charges (subscriptions, bills) and their accounts.

    Args:
        start_date: Window start, YYYY-MM-DD. Defaults to today.
        end_date: Window end, YYYY-MM-DD. Defaults to 30 days out.
    """
    today = date.today()
    start_date = start_date or today.isoformat()
    end_date = end_date or (today + timedelta(days=30)).isoformat()
    client = await _get_client()
    return _dump(
        await client.get_recurring_transactions(
            start_date=start_date, end_date=end_date
        )
    )


@mcp.tool()
async def investment_holdings() -> str:
    """List investment holdings across all brokerage accounts, with quantity and value."""
    client = await _get_client()
    return _dump(await client.get_all_holdings())


@mcp.tool()
async def find_duplicate_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_ids: Optional[list[str]] = None,
) -> str:
    """Find groups of likely-duplicate transactions (same date, amount, and merchant).

    Args:
        start_date: Window start, YYYY-MM-DD. Defaults to 90 days ago.
        end_date: Window end, YYYY-MM-DD. Defaults to today.
        account_ids: Restrict the scan to these accounts.
    """
    client = await _get_client()
    start_date, end_date = _default_range(start_date, end_date, days=90)
    return _dump(
        await client.find_duplicate_transactions(
            start_date=start_date, end_date=end_date, account_ids=account_ids
        )
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
