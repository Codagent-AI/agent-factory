"""Serve a Python stand-in for a command-line tool from the test process.

A stand-in written as a Python script starts an interpreter on every call, about
15 ms of CPU. The end-to-end journeys call their GitHub stand-in some 15 times per
tick, and on a small CI runner those interpreters were a large share of the suite's
CPU. `ScriptServer` runs the same script source inside the test process instead,
behind a shell client that costs about 4 ms. The client sends its arguments,
whether GH_TOKEN is set, and the standard input of a call that passes `--input`
over a Unix socket, then replays the answer's standard output, standard error, and
exit status. Calls run one at a time.
"""

from __future__ import annotations

import builtins
import io
import os
import socket
import tempfile
import threading
import traceback
import weakref
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from tests.fixtures.executables import install

# The same for every stand-in, so it can be installed as a shared file: the socket's
# path is in a file beside the executable.
_CLIENT = """#!/bin/sh
# Stand-in client: the program runs in the test process (tests/fixtures/stub_server.py).
read -r socket < "${0%/*}/.${0##*/}.socket" || exit 1
input=
for argument; do [ "$argument" = --input ] && input=1; done
reply=$({ printf '%s\\0' "${GH_TOKEN:+1}" "$#" "$@"; [ -z "$input" ] || cat; printf '\\0'; } |
  nc -U "$socket") || exit 1
[ -n "$reply" ] || { echo "stand-in server did not answer" >&2; exit 1; }
eval "$reply"
"""


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


class _Environment:
    """The `os` module as one call sees it: only GH_TOKEN, and only whether it is set."""

    def __init__(self, token: bool) -> None:
        self.environ = {"GH_TOKEN": "set"} if token else {}

    def __getattr__(self, name: str) -> object:
        return getattr(os, name)


class ScriptServer:
    """Answer a stand-in's calls by running its Python source in this process."""

    def __init__(self, source: str, program: str) -> None:
        self._code = compile(source, program, "exec")
        self._program = program
        self._lock = threading.Lock()
        self._directory = Path(tempfile.mkdtemp(prefix="stub-", dir="/tmp"))
        # Unix socket paths are limited to about 100 bytes, so not under tmp_path.
        self.socket_path = self._directory / "s"
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(self.socket_path))
        self._socket.listen(16)
        threading.Thread(target=self._serve, daemon=True).start()
        self._finalizer = weakref.finalize(self, _close, self._socket, self._directory)

    def install(self, executable: Path) -> None:
        """Install the stand-in's shell client at ``executable``."""
        (executable.parent / f".{executable.name}.socket").write_text(f"{self.socket_path}\n")
        install(executable, _CLIENT)

    def close(self) -> None:
        self._finalizer()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            data = b""
            while True:
                fields = data.split(b"\0")
                if len(fields) > 2 and len(fields) >= int(fields[1] or 0) + 4:
                    break
                chunk = connection.recv(65536)
                if not chunk:
                    return
                data += chunk
            count = int(fields[1])
            arguments = [field.decode() for field in fields[2 : 2 + count]]
            body = fields[2 + count].decode()
            with self._lock:
                status, output, error = self._run(arguments, body, token=fields[0] == b"1")
            reply = (
                f"printf '%s' {_quoted(output)}; printf '%s' {_quoted(error)} >&2; exit {status}"
            )
            connection.sendall(reply.encode())

    def _run(self, arguments: list[str], body: str, *, token: bool) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()

        def leave(code: object = None) -> None:
            raise SystemExit(code)

        call_sys = SimpleNamespace(
            argv=[self._program, *arguments],
            stdin=io.StringIO(body),
            stdout=output,
            stderr=error,
            exit=leave,
        )
        call_os = _Environment(token)
        real_import = cast(Callable[..., object], builtins.__import__)

        def isolated_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "sys":
                return call_sys
            if name == "os":
                return call_os
            return real_import(name, *args, **kwargs)

        def isolated_print(*values: object, **kwargs: object) -> None:
            kwargs.setdefault("file", output)
            print(*values, **kwargs)  # type: ignore[call-overload]

        namespace: dict[str, object] = {
            "__name__": "__main__",
            "__builtins__": {
                **vars(builtins),
                "__import__": isolated_import,
                "print": isolated_print,
            },
        }
        try:
            exec(self._code, namespace)  # noqa: S102 - the test's own stand-in source
        except SystemExit as stop:
            code = stop.code
            if code is None:
                return 0, output.getvalue(), error.getvalue()
            if isinstance(code, int):
                return code, output.getvalue(), error.getvalue()
            error.write(f"{code}\n")
            return 1, output.getvalue(), error.getvalue()
        except Exception:  # noqa: BLE001 - reported the way an uncaught error would be
            error.write(traceback.format_exc())
            return 1, output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()


def _close(listener: socket.socket, directory: Path) -> None:
    listener.close()
    for path in directory.iterdir():
        path.unlink()
    directory.rmdir()
