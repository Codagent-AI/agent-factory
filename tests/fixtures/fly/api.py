"""In-memory HTTP fake for Machines API and registry-manifest tests."""

from __future__ import annotations

import json
import threading
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeMachinesApi(AbstractContextManager["FakeMachinesApi"]):
    def __init__(self) -> None:
        self.machines: dict[str, dict[str, object]] = {}
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> FakeMachinesApi:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self._thread.join()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def _body(self) -> dict[str, object] | None:
                size = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(size)) if size else None

            def _send(self, status: int, value: object = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if value is not None:
                    self.wfile.write(json.dumps(value).encode())

            def do_GET(self) -> None:  # noqa: N802
                fake.requests.append(("GET", self.path, None))
                if self.path.endswith("/machines") or "?/" in self.path:
                    self._send(200, list(fake.machines.values()))
                elif "/machines/" in self.path:
                    value = fake.machines.get(self.path.rsplit("/", 1)[-1])
                    self._send(200, value) if value else self._send(404)
                else:
                    self._send(200, {"name": "fake-app"})

            def do_POST(self) -> None:  # noqa: N802
                body = self._body()
                fake.requests.append(("POST", self.path, body))
                if self.path.endswith("/machines"):
                    machine_id = f"machine-{len(fake.machines) + 1}"
                    machine = {"id": machine_id, "state": "started", **(body or {})}
                    fake.machines[machine_id] = machine
                    self._send(200, machine)
                else:
                    self._send(200, {})

            def do_DELETE(self) -> None:  # noqa: N802
                fake.requests.append(("DELETE", self.path, None))
                machine_id = self.path.rsplit("/", 1)[-1]
                self._send(200, fake.machines.pop(machine_id, None))

        return Handler
