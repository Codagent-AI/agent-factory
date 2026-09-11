from __future__ import annotations

import errno
import io
import json
import socket
import ssl
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from agent_factory.github import (
    AppCredentials,
    GitHubApiError,
    InstallationTokenProvider,
    SubprocessGhRunner,
)


def _signed_jwt(*args: object) -> str:
    return "signed.jwt.value"


def _unauthorized(*args: object) -> str:
    return '{"message": "unauthorized"}'


@pytest.mark.parametrize("host", ["github.com", "github.example.org"])
def test_installation_exchange_sends_bearer_header_and_caches_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str
) -> None:
    received: list[str | None] = []
    monkeypatch.setenv("GH_HOST", host)

    class GitHubEndpoint(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.headers.get("Authorization"))
            self.send_response(201 if received[-1] == "Bearer signed.jwt.value" else 401)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"token": "installation-token", "expires_at": "2099-01-01T00:00:00Z"}
                ).encode()
            )

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), GitHubEndpoint)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def route_to_local_endpoint(request: Request, *, timeout: float) -> object:
        base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        assert request.full_url == f"{base}/app/installations/123/access_tokens"
        assert request.get_method() == "POST"
        assert 0 < timeout <= 60
        local = Request(
            f"http://127.0.0.1:{server.server_port}/access_tokens",
            data=request.data,
            headers=dict(request.header_items()),
            method=request.get_method(),
        )
        return urlopen(local, timeout=timeout)

    def reject_gh_jwt(*args: object, **kwargs: object) -> str:
        raise GitHubApiError("GitHub rejected gh's token authentication scheme")

    monkeypatch.setattr("urllib.request.urlopen", route_to_local_endpoint)
    monkeypatch.setattr(InstallationTokenProvider, "_signed_jwt", _signed_jwt)
    monkeypatch.setattr(SubprocessGhRunner, "run", reject_gh_jwt)
    provider = InstallationTokenProvider(AppCredentials("456", "123", tmp_path / "key.pem"))
    try:
        assert provider() == "installation-token"
        assert provider() == "installation-token"
        assert received == ["Bearer signed.jwt.value"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_installation_exchange_reports_http_status_without_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def reject(request: Request, *, timeout: float) -> object:
        raise HTTPError(request.full_url, 401, "signed.jwt.value", Message(), io.BytesIO(b"secret"))

    monkeypatch.setattr("urllib.request.urlopen", reject)
    monkeypatch.setattr(InstallationTokenProvider, "_signed_jwt", _signed_jwt)
    monkeypatch.setattr(SubprocessGhRunner, "run", _unauthorized)
    provider = InstallationTokenProvider(AppCredentials("456", "123", tmp_path / "key.pem"))
    with pytest.raises(
        GitHubApiError, match="installation token exchange failed: HTTP 401"
    ) as error:
        provider()
    assert "signed.jwt.value" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    ("cause", "detail"),
    [
        (socket.gaierror(-2, "private proxy details"), "DNS resolution failed"),
        (
            ssl.SSLCertVerificationError(1, "private certificate details"),
            "TLS certificate verification failed",
        ),
        (TimeoutError("private endpoint details"), "connection timed out"),
        (
            ConnectionRefusedError(errno.ECONNREFUSED, "private endpoint details"),
            "Connection refused",
        ),
    ],
)
def test_token_exchange_reports_safe_network_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cause: OSError, detail: str
) -> None:
    def fail(request: Request, *, timeout: float) -> object:
        raise URLError(cause)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr(InstallationTokenProvider, "_signed_jwt", _signed_jwt)
    provider = InstallationTokenProvider(AppCredentials("456", "123", tmp_path / "key.pem"))
    with pytest.raises(GitHubApiError, match=detail) as error:
        provider()
    assert "private" not in str(error.value)
