"""Hand a Machine the Codex subscription as an access token, never as the whole login.

OpenAI refresh tokens are single use. A Machine that holds a copy of the Mac's
``auth.json`` and refreshes it cancels the Mac's copy, and the new token is lost
when the Machine is destroyed. Archon reads a ChatGPT access token and account id
from ``ARCHON_CODEX_CHATGPT_ACCESS_TOKEN`` and ``ARCHON_CODEX_CHATGPT_ACCOUNT_ID``,
logs each Codex turn in with them in memory, and never refreshes them. The eval's
Codex judge runs ``codex exec`` itself, so the Machine also gets an ``auth.json``
whose refresh token is empty. The refresh token stays on the Mac, and only the
Mac's own Codex refreshes it, saving the new tokens back into ``auth.json``.
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import fcntl
import json
import os
import queue
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

ACCESS_TOKEN_ENV = "ARCHON_CODEX_CHATGPT_ACCESS_TOKEN"
ACCOUNT_ID_ENV = "ARCHON_CODEX_CHATGPT_ACCOUNT_ID"
# A delivered token must outlive at least this long, even for a shorter job.
MINIMUM_LIFETIME_SECONDS = 24 * 3600
_REFRESH_TIMEOUT_SECONDS = 60


class CodexLoginError(RuntimeError):
    """Why the login cannot be delivered. The message never holds a token."""


@dataclass(frozen=True)
class CodexLogin:
    id_token: str = field(repr=False)
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)
    expires_at: int

    def environment_lines(self) -> str:
        """The env-file lines the guest sources before it runs Archon."""
        return (
            f"{ACCESS_TOKEN_ENV}={shlex.quote(self.access_token)}\n"
            f"{ACCOUNT_ID_ENV}={shlex.quote(self.account_id)}\n"
        )

    def login_file(self, now: datetime.datetime) -> bytes:
        """An ``auth.json`` without the refresh token, for Codex run directly on the Machine.

        ``last_refresh`` is the delivery time, so Codex sees a fresh login and does not
        try to refresh it before the access token expires.
        """
        tokens = {
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": "",
            "account_id": self.account_id,
        }
        stamp = now.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        login = {"OPENAI_API_KEY": None, "tokens": tokens, "last_refresh": stamp}
        return json.dumps(login).encode()


def _get(value: object, key: str) -> object:
    return cast(dict[str, object], value).get(key) if isinstance(value, dict) else None


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured) if configured else Path.home() / ".codex"


def _expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        claims: object = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise CodexLoginError("the Codex access token has no readable expiry") from None
    expires = _get(claims, "exp")
    if not isinstance(expires, int) or isinstance(expires, bool):
        raise CodexLoginError("the Codex access token has no readable expiry")
    return expires


def read_login(home: Path) -> CodexLogin:
    path = home / "auth.json"
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CodexLoginError("the Codex login file is missing: run `codex login`") from None
    except (OSError, ValueError):
        raise CodexLoginError("the Codex login file is unreadable") from None
    tokens = _get(data, "tokens")
    fields = [_get(tokens, key) for key in ("id_token", "access_token", "account_id")]
    if not all(isinstance(value, str) and value for value in fields):
        raise CodexLoginError("the Codex login is not a ChatGPT sign-in: run `codex login`")
    id_token, access, account = cast(list[str], fields)
    return CodexLogin(id_token, access, account, _expiry(access))


def refresh_native_login(codex: str = "codex") -> None:
    """Ask the Mac's Codex to refresh its login; Codex saves the new tokens itself.

    The app-server stops reading at end of input without answering what is still
    pending, so each reply is awaited before stdin closes.
    """
    try:
        process = subprocess.Popen(
            [codex, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        raise CodexLoginError("the Codex CLI could not be started to refresh the login") from None
    assert process.stdin is not None and process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()
    stdout = process.stdout

    def read() -> None:
        for line in stdout:
            lines.put(line)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + _REFRESH_TIMEOUT_SECONDS

    def call(identifier: int, method: str, params: object) -> object:
        assert process.stdin is not None
        process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}))
        process.stdin.write("\n")
        process.stdin.flush()
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise CodexLoginError("the Codex login refresh timed out") from None
            try:
                message: object = json.loads(line)
            except ValueError:
                continue
            if _get(message, "id") == identifier:
                return message

    try:
        call(1, "initialize", {"clientInfo": {"name": "agent-factory"}, "capabilities": None})
        process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        reply = call(2, "account/read", {"refreshToken": True})
    except (BrokenPipeError, OSError):
        raise CodexLoginError("the Codex CLI stopped while refreshing the login") from None
    finally:
        with contextlib.suppress(OSError):
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if not _get(_get(reply, "result"), "account"):
        raise CodexLoginError(
            "the Codex login on this Mac could not be refreshed: run `codex login`"
        )


def delivered_login(
    minimum_seconds: int,
    *,
    home: Path | None = None,
    now: Callable[[], float] = time.time,
    refresh: Callable[[], None] = refresh_native_login,
) -> CodexLogin:
    """The Mac's login, refreshed first if its access token would expire too soon.

    A lock keeps two launches from refreshing at once: the second would spend a
    refresh token the first had already used.
    """
    home = home or codex_home()
    lock = Path(tempfile.gettempdir()) / f"agent-factory-codex-login-{os.getuid()}.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        login = read_login(home)
        if login.expires_at - now() >= minimum_seconds:
            return login
        refresh()
        login = read_login(home)
        if login.expires_at - now() < minimum_seconds:
            raise CodexLoginError(
                "the refreshed Codex access token would expire before the job ends"
            )
        return login
