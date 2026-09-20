"""Narrow, dependency-free client for the Fly Machines and registry APIs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FlyApiError(RuntimeError):
    """An API failure which identifies its path but never leaks its bearer token."""

    def __init__(
        self, path: str, status: int | None = None, detail: str = "request failed"
    ) -> None:
        self.path = path
        self.status = status
        super().__init__(f"Fly API request failed for {path}: {detail}")


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


class FlyMachinesClient:
    def __init__(
        self,
        app: str,
        token_file: Path,
        *,
        base_url: str | None = None,
        registry_base_url: str = "https://registry.fly.io",
    ) -> None:
        self.app = app
        self.token_file = token_file
        # An explicit URL wins. The environment override exists so the launcher,
        # which builds its own client from a manifest, can be pointed at a local fake.
        self.base_url = (
            base_url or os.environ.get("AGENT_FACTORY_FLY_API_URL") or "https://api.machines.dev"
        ).rstrip("/")
        self._cached_token: str | None = None
        self.registry_base_url = registry_base_url.rstrip("/")

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
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- configured API endpoint
                raw = response.read()
                return cast(
                    Mapping[str, object] | list[object] | None, json.loads(raw) if raw else None
                )
        except HTTPError as error:
            raise FlyApiError(path, error.code, f"HTTP {error.code}") from error
        except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FlyApiError(path, detail="request could not be completed") from error

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
                "auto_destroy": True,
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
        request.add_header("Authorization", f"Bearer {self._token()}")
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310
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
