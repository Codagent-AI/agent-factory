"""Narrow, dependency-free client for the Fly Machines and registry APIs."""

from __future__ import annotations

import json
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


class FlyMachinesClient:
    def __init__(
        self, app: str, token_file: Path, *, base_url: str = "https://api.machines.dev"
    ) -> None:
        self.app = app
        self.token_file = token_file
        self.base_url = base_url.rstrip("/")

    def _token(self) -> str:
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise FlyApiError("token", detail="deploy token file is unreadable") from error
        if not token or "\n" in token or "\r" in token:
            raise FlyApiError("token", detail="deploy token file must contain one token")
        return token

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Mapping[str, object] | None = None,
        url: str | None = None,
    ) -> Mapping[str, object] | list[object] | None:
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(url or f"{self.base_url}{path}", data=payload, method=method)
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 -- configured API endpoint
                raw = response.read()
                return cast(
                    Mapping[str, object] | list[object] | None, json.loads(raw) if raw else None
                )
        except HTTPError as error:
            raise FlyApiError(path, error.code, f"HTTP {error.code}") from error
        except (URLError, OSError, json.JSONDecodeError) as error:
            raise FlyApiError(path, detail="request could not be completed") from error

    def create_machine(
        self,
        *,
        image: str,
        cpu_kind: str,
        cpus: int,
        memory_mb: int,
        region: str,
        metadata: Mapping[str, str],
        env: Mapping[str, str],
        guest_init: str,
    ) -> Mapping[str, object]:
        body: dict[str, object] = {
            "image": image,
            "guest": {
                "cpu_kind": cpu_kind,
                "cpus": cpus,
                "memory_mb": memory_mb,
                "persist_rootfs": "always",
            },
            "auto_destroy": True,
            "restart": {"policy": "no"},
            "region": region,
            "metadata": dict(metadata),
            "env": dict(env),
            "init": {"exec": ["bash", "-c", guest_init]},
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

    def update_config(self, machine_id: str, config: Mapping[str, object]) -> Mapping[str, object]:
        return _mapping(
            self._request(
                f"/v1/apps/{self.app}/machines/{machine_id}",
                method="POST",
                body={"config": dict(config)},
            )
        )

    def stop(self, machine_id: str) -> None:
        self._request(f"/v1/apps/{self.app}/machines/{machine_id}/stop", method="POST")

    def start(self, machine_id: str) -> None:
        self._request(f"/v1/apps/{self.app}/machines/{machine_id}/start", method="POST")

    def destroy(self, machine_id: str) -> None:
        path = f"/v1/apps/{self.app}/machines/{machine_id}"
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
        request = Request(f"https://registry.fly.io{path}", method="HEAD")
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
