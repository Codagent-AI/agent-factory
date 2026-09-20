"""In-memory HTTP fake for Machines API and registry-manifest tests."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlsplit


class FakeMachinesApi(AbstractContextManager["FakeMachinesApi"]):
    """Small stateful API fake; ``requests`` retains method, path, headers, and body."""

    def __init__(self, *, manifest_digest: str = "sha256:fake") -> None:
        self.machines: dict[str, dict[str, object]] = {}
        self.requests: list[dict[str, object]] = []
        self.manifest_digest = manifest_digest
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
                return cast(dict[str, object], json.loads(self.rfile.read(size))) if size else None

            def _record(self, body: dict[str, object] | None = None) -> None:
                fake.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "body": body,
                    }
                )

            def _send(self, status: int, value: object = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if value is not None:
                    self.wfile.write(json.dumps(value).encode())

            def do_HEAD(self) -> None:  # noqa: N802
                self._record()
                if urlsplit(self.path).path.startswith("/v2/"):
                    self.send_response(200)
                    self.send_header("Docker-Content-Digest", fake.manifest_digest)
                    self.end_headers()
                else:
                    self._send(404)

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                parsed = urlsplit(self.path)
                if parsed.path.endswith("/machines"):
                    filters = parse_qs(parsed.query)
                    machines = list(fake.machines.values())
                    for key, values in filters.items():
                        if key.startswith("metadata."):
                            metadata_key = key.removeprefix("metadata.")
                            wanted = values[0]
                            machines = [
                                machine
                                for machine in machines
                                if _metadata(machine).get(metadata_key) == wanted
                            ]
                    self._send(200, machines)
                elif parsed.path.endswith("/wait"):
                    known = _machine_id(parsed.path) in fake.machines
                    self._send(200, {"ok": True}) if known else self._send(404)
                elif "/machines/" in parsed.path:
                    value = fake.machines.get(parsed.path.rsplit("/", 1)[-1])
                    self._send(200, value) if value else self._send(404)
                elif parsed.path.startswith("/v1/apps/"):
                    self._send(200, {"name": "fake-app"})
                else:
                    self._send(404)

            def do_POST(self) -> None:  # noqa: N802
                body = self._body()
                self._record(body)
                path = urlsplit(self.path).path
                if path.endswith("/machines"):
                    machine_id = f"machine-{len(fake.machines) + 1}"
                    config = cast(dict[str, object], body.get("config", {}) if body else {})
                    machine: dict[str, object] = {
                        "id": machine_id,
                        "state": "started",
                        "config": config,
                    }
                    fake.machines[machine_id] = machine
                    self._send(200, machine)
                    return
                machine_id = _machine_id(path)
                existing = fake.machines.get(machine_id)
                if existing is None:
                    self._send(404)
                elif path.endswith("/stop"):
                    existing["state"] = "stopped"
                    self._send(200, existing)
                elif path.endswith("/start"):
                    existing["state"] = "started"
                    self._send(200, existing)
                elif "/metadata/" in path:
                    key = path.rsplit("/", 1)[-1]
                    value = body.get("value") if body else None
                    _metadata(existing)[key] = str(value)
                    self._send(200, existing)
                else:
                    config = body.get("config") if body else None
                    if isinstance(config, Mapping):
                        _config(existing).update(cast(Mapping[str, object], config))
                    self._send(200, existing)

            def do_DELETE(self) -> None:  # noqa: N802
                self._record()
                machine_id = _machine_id(urlsplit(self.path).path)
                if machine_id not in fake.machines:
                    self._send(404)
                else:
                    self._send(200, fake.machines.pop(machine_id))

        return Handler


def _machine_id(path: str) -> str:
    parts = path.split("/machines/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 else ""


def _config(machine: dict[str, object]) -> dict[str, object]:
    config = machine.setdefault("config", {})
    return cast(dict[str, object], config)


def _metadata(machine: dict[str, object]) -> dict[str, str]:
    metadata = _config(machine).setdefault("metadata", {})
    return cast(dict[str, str], metadata)
