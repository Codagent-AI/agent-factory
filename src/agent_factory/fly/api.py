"""Narrow, dependency-free client for the Fly Machines and registry APIs."""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RefuseRedirects(HTTPRedirectHandler):
    # urllib forwards the Authorization header to a redirect target, which would hand
    # the deploy token to whatever host the response names. Neither API redirects.
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


_OPENER = build_opener(_RefuseRedirects)


class FlyApiError(RuntimeError):
    """An API failure which identifies its path but never leaks its bearer token."""

    def __init__(
        self, path: str, status: int | None = None, detail: str = "request failed"
    ) -> None:
        self.path = path
        self.status = status
        super().__init__(f"Fly API request failed for {path}: {detail}")


_MANIFEST_MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)

_RATE_LIMIT_ATTEMPTS = 6


def _retry_after(header: str | None, attempt: int) -> float:
    """Honour the server's hint when it gives one, else back off 1, 2, 4... seconds."""
    if header and header.strip().isdigit():
        return min(float(header.strip()), 30.0) + attempt
    return float(min(2**attempt, 30))


GONE_STATES = frozenset({"destroyed", "destroying"})


def is_gone(machine: Mapping[str, object]) -> bool:
    """Fly keeps answering for a destroyed Machine for a while."""
    return machine.get("state") in GONE_STATES


def read_token(token_file: Path) -> str:
    """The one reader of the deploy token, shared by REST and flyctl callers."""
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise FlyApiError("token", detail="deploy token file is unreadable") from error
    if not token or "\n" in token or "\r" in token:
        raise FlyApiError("token", detail="deploy token file must contain one token")
    return token


def _encrypted_endpoint(url: str) -> str:
    """The deploy token rides every request, so only a loopback fake may use HTTP."""
    parsed = urlsplit(url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError(f"Fly endpoint must use HTTPS: {parsed.scheme}://{parsed.hostname}")
    return url.rstrip("/")


class FlyMachinesClient:
    def __init__(
        self,
        app: str,
        token_file: Path,
        *,
        base_url: str | None = None,
        registry_base_url: str = "https://registry.fly.io",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self.app = app
        self.token_file = token_file
        # An explicit URL wins. The environment override exists so the launcher,
        # which builds its own client from a manifest, can be pointed at a local fake.
        self.base_url = _encrypted_endpoint(
            base_url or os.environ.get("AGENT_FACTORY_FLY_API_URL") or "https://api.machines.dev"
        )
        self._cached_token: str | None = None
        self.registry_base_url = _encrypted_endpoint(registry_base_url)

    def _token(self) -> str:
        # A deploy token is static for the life of a client; read it once.
        if self._cached_token is None:
            self._cached_token = read_token(self.token_file)
        return self._cached_token

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Mapping[str, object] | None = None,
        url: str | None = None,
        timeout: int = 20,
    ) -> Mapping[str, object] | list[object] | None:
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(url or f"{self.base_url}{path}", data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        # Fly limits requests per Machine and per action, so consecutive writes to one
        # Machine can draw HTTP 429. Those are retried with a growing pause; any
        # other failure is reported at once.
        for attempt in range(_RATE_LIMIT_ATTEMPTS):
            try:
                with _OPENER.open(request, timeout=timeout) as response:
                    raw = response.read()
                    return cast(
                        Mapping[str, object] | list[object] | None,
                        json.loads(raw) if raw else None,
                    )
            except HTTPError as error:
                if error.code != 429 or attempt == _RATE_LIMIT_ATTEMPTS - 1:
                    raise FlyApiError(path, error.code, f"HTTP {error.code}") from error
                self._sleep(_retry_after(error.headers.get("Retry-After"), attempt))
            except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FlyApiError(path, detail="request could not be completed") from error
        raise FlyApiError(path, 429, "HTTP 429")  # unreachable; keeps the return type total

    def create_machine(
        self,
        *,
        image: str,
        cpu_kind: str,
        cpus: int,
        memory_mb: int,
        region: str,
        factory_owner: str,
        run_id: str,
        claim_id: str,
        nonce: str,
        deadline_epoch: str,
        unit_key: str,
        guest_init: str,
    ) -> Mapping[str, object]:
        body: dict[str, object] = {
            "region": region,
            "config": {
                "image": image,
                "guest": {
                    "cpu_kind": cpu_kind,
                    "cpus": cpus,
                    "memory_mb": memory_mb,
                    "persist_rootfs": "always",
                },
                # On real Fly this also fires on an API stop, which would destroy a
                # Machine held for a quota reset. The guest ends its own process at
                # the deadline and the reconciler performs every destroy.
                "auto_destroy": False,
                "restart": {"policy": "no"},
                "metadata": {
                    "factory-owner": factory_owner,
                    "run_id": run_id,
                    "claim_id": claim_id,
                    "nonce": nonce,
                    "deadline_epoch": deadline_epoch,
                    "unit_key": unit_key,
                },
                "env": {
                    "FACTORY_DEADLINE_EPOCH": deadline_epoch,
                    "FACTORY_RUN_ID": run_id,
                    "FACTORY_NONCE": nonce,
                },
                "init": {"exec": ["bash", "-c", guest_init]},
            },
        }
        result = self._request(f"/v1/apps/{self.app}/machines", method="POST", body=body)
        return _mapping(result)

    def get_machine(self, machine_id: str) -> Mapping[str, object]:
        return _mapping(self._request(f"/v1/apps/{self.app}/machines/{machine_id}"))

    def list_machines(self, metadata_key: str, metadata_value: str) -> list[Mapping[str, object]]:
        path = f"/v1/apps/{self.app}/machines?metadata.{metadata_key}={metadata_value}"
        result = self._request(path)
        return [_mapping(item) for item in result] if isinstance(result, list) else []

    def set_metadata(self, machine_id: str, key: str, value: str) -> None:
        self._request(
            f"/v1/apps/{self.app}/machines/{machine_id}/metadata/{key}",
            method="POST",
            body={"value": value},
        )

    def update_config(
        self, machine_id: str, config: Mapping[str, object], *, skip_launch: bool = False
    ) -> Mapping[str, object]:
        # Fly starts a Machine on update unless told otherwise; a stopped Machine
        # must receive its new deadline before it boots.
        body: dict[str, object] = {"config": dict(config)}
        if skip_launch:
            body["skip_launch"] = True
        return _mapping(
            self._request(f"/v1/apps/{self.app}/machines/{machine_id}", method="POST", body=body)
        )

    def update_stopped_deadline(
        self, machine_id: str, deadline_epoch: int, metadata: Mapping[str, str] | None = None
    ) -> Mapping[str, object]:
        """Give a stopped Machine its next deadline without booting it.

        A config update replaces the whole config, so the observed config is the
        base; only the deadline env and the named metadata keys change.
        """
        observed = self.get_machine(machine_id).get("config")
        config = dict(cast(Mapping[str, object], observed)) if isinstance(observed, Mapping) else {}
        env = config.get("env")
        merged_env = dict(cast(Mapping[str, object], env)) if isinstance(env, Mapping) else {}
        merged_env["FACTORY_DEADLINE_EPOCH"] = str(deadline_epoch)
        config["env"] = merged_env
        existing = config.get("metadata")
        merged_metadata = (
            dict(cast(Mapping[str, object], existing)) if isinstance(existing, Mapping) else {}
        )
        merged_metadata["deadline_epoch"] = str(deadline_epoch)
        merged_metadata.update(metadata or {})
        config["metadata"] = merged_metadata
        return self.update_config(machine_id, config, skip_launch=True)

    def wait_state(self, machine_id: str, state: str, *, timeout_seconds: int = 60) -> bool:
        path = (
            f"/v1/apps/{self.app}/machines/{machine_id}/wait"
            f"?state={state}&timeout={timeout_seconds}"
        )
        try:
            self._request(path, timeout=timeout_seconds + 10)
        except FlyApiError as error:
            if error.status in {408, 504}:
                return False
            raise
        return True

    def stop(self, machine_id: str) -> None:
        self._request(f"/v1/apps/{self.app}/machines/{machine_id}/stop", method="POST")

    def start(self, machine_id: str) -> None:
        self._request(f"/v1/apps/{self.app}/machines/{machine_id}/start", method="POST")

    def destroy(self, machine_id: str) -> None:
        # Fly refuses to delete a started Machine without force.
        path = f"/v1/apps/{self.app}/machines/{machine_id}?force=true"
        try:
            self._request(path, method="DELETE")
        except FlyApiError as error:
            if error.status != 404:
                raise

    def get_app(self) -> Mapping[str, object]:
        return _mapping(self._request(f"/v1/apps/{self.app}"))

    def resolve_manifest(self, image: str) -> str:
        repository, tag = (
            image.rsplit(":", 1) if ":" in image.rsplit("/", 1)[-1] else (image, "latest")
        )
        repo = repository.removeprefix("registry.fly.io/")
        path = f"/v2/{repo}/manifests/{tag}"
        request = Request(f"{self.registry_base_url}{path}", method="HEAD")
        # registry.fly.io rejects a bearer token; it takes HTTP basic auth with any
        # user name and the token as the password.
        credentials = base64.b64encode(f"x:{self._token()}".encode()).decode()
        request.add_header("Authorization", f"Basic {credentials}")
        request.add_header("Accept", ", ".join(_MANIFEST_MEDIA_TYPES))
        try:
            with _OPENER.open(request, timeout=20) as response:
                digest = response.headers.get("Docker-Content-Digest")
        except HTTPError as error:
            raise FlyApiError(path, error.code, f"HTTP {error.code}") from error
        except (URLError, OSError) as error:
            raise FlyApiError(path, detail="manifest could not be resolved") from error
        if not digest:
            raise FlyApiError(path, detail="registry did not return a digest")
        return digest


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FlyApiError("response", detail="API returned an unexpected response")
    return cast(Mapping[str, object], value)
