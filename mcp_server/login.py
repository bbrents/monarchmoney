"""One-time interactive login that creates the saved Monarch session.

Run this once from a terminal:

    .venv\\Scripts\\python.exe mcp_server\\login.py

It prompts for email, password, and an MFA code if needed, then writes a session
pickle to the absolute path the MCP server reads. After this succeeds the server
needs no credentials in its config at all.

Re-run it whenever the session expires and tools start failing to authenticate.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

from monarchmoney import LoginFailedException, MonarchMoney, RequireMFAException

_REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE") or str(
    _REPO_ROOT / ".mm" / "mm_session.pickle"
)


async def main() -> int:
    print(f"Session will be saved to: {SESSION_FILE}\n")

    if os.path.exists(SESSION_FILE):
        answer = input("A session already exists. Replace it? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Keeping the existing session. Nothing changed.")
            return 0

    email = input("Monarch email: ").strip()
    password = getpass.getpass("Monarch password: ")

    mm = MonarchMoney(session_file=SESSION_FILE)

    try:
        await mm.login(
            email=email,
            password=password,
            use_saved_session=False,
            save_session=True,
        )
    except RequireMFAException:
        code = input("Two-factor code: ").strip()
        try:
            # trusted_device=True requests a long-lived, browser-style session
            # so this login survives longer than an hour.
            await mm.multi_factor_authenticate(
                email, password, code, trusted_device=True
            )
            mm.save_session(SESSION_FILE)
        except LoginFailedException as exc:
            print(f"\nMFA failed: {exc}", file=sys.stderr)
            return 1
    except LoginFailedException as exc:
        print(f"\nLogin failed: {exc}", file=sys.stderr)
        return 1

    # Prove the saved credentials actually work before declaring success.
    try:
        accounts = await mm.get_accounts()
    except Exception as exc:
        print(f"\nLogged in, but the test request failed: {exc}", file=sys.stderr)
        return 1

    count = len(accounts.get("accounts", []))
    print(f"\nSuccess. Session saved; {count} account(s) visible.")
    print("You can now start the MCP server from Claude Desktop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
