"""Narrow, dependency-free client for the Fly Machines and registry APIs."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
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
        self,
        path: str,
        status: int | None = None,
        detail: str = "request failed",
        reason: str = "",
    ) -> None:
        self.path = path
        self.status = status
        # Fly's own one-line explanation, when it gave one.
        self.reason = reason
        super().__init__(f"Fly API request failed for {path}: {detail}")


_MANIFEST_MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-fA-F]{64}")

_RATE_LIMIT_ATTEMPTS = 6
# Fly answers 400 "failed to get manifest ..." to a create whose image it cannot
# pull yet. An image pushed seconds earlier can take about a minute to become
# pullable, so that one failure is retried for about 100 seconds; any other 400
# is reported at once.
_UNPULLABLE_IMAGE = "failed to get manifest"
_CREATE_ATTEMPTS = 5
_CREATE_RETRY_SECONDS = 10.0


def _http_reason(error: HTTPError) -> str:
    """Fly's own one-line reason for a failure, which never echoes the token."""
    try:
        raw = error.read(2048).decode("utf-8", "replace")
    except OSError:
        raw = ""
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        parsed = raw
    if isinstance(parsed, Mapping):
        parsed = cast(Mapping[str, object], parsed).get("error", "")
    return " ".join(str(parsed).split())[:300] if parsed else ""


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
                    raise _http_error(path, error) from error
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
        path = f"/v1/apps/{self.app}/machines"
        for attempt in range(_CREATE_ATTEMPTS):
            try:
                return _mapping(self._request(path, method="POST", body=body))
            except FlyApiError as error:
                unpullable = error.status == 400 and error.reason.startswith(_UNPULLABLE_IMAGE)
                if not unpullable or attempt == _CREATE_ATTEMPTS - 1:
                    raise
                self._sleep(_CREATE_RETRY_SECONDS * (attempt + 1))
        raise FlyApiError(path, 400, "HTTP 400")  # unreachable; keeps the return type total

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

    def _registry_request(self, url: str, method: str) -> Request:
        request = Request(url, method=method)
        # registry.fly.io rejects a bearer token; it takes HTTP basic auth with any
        # user name and the token as the password.
        credentials = base64.b64encode(f"x:{self._token()}".encode()).decode()
        request.add_header("Authorization", f"Basic {credentials}")
        return request

    def resolve_manifest(self, image: str) -> str:
        if "@" in image:
            repository, tag = image.split("@", 1)
        else:
            repository, tag = (
                image.rsplit(":", 1) if ":" in image.rsplit("/", 1)[-1] else (image, "latest")
            )
        repo = repository.removeprefix("registry.fly.io/")
        path = f"/v2/{repo}/manifests/{tag}"
        request = self._registry_request(f"{self.registry_base_url}{path}", "GET")
        request.add_header("Accept", ", ".join(_MANIFEST_MEDIA_TYPES))
        try:
            with _OPENER.open(request, timeout=20) as response:
                digest = response.headers.get("Docker-Content-Digest")
        except HTTPError as error:
            raise _http_error(path, error) from error
        except (URLError, OSError) as error:
            raise FlyApiError(path, detail="manifest could not be resolved") from error
        if not digest:
            raise FlyApiError(path, detail="registry did not return a digest")
        return digest

    def list_tags(self, repository: str) -> list[str]:
        repo = repository.removeprefix("registry.fly.io/")
        path = f"/v2/{repo}/tags/list"
        url = f"{self.registry_base_url}{path}"
        tags: list[str] = []
        visited: set[str] = set()
        # Fly names the repository by an internal id in the listing; every page must name the
        # same one, so a page from another repository cannot hide a tag sharing a digest.
        listed_name: str | None = None
        while True:
            if url in visited:
                raise FlyApiError(path, detail="registry repeated a tag-list page")
            visited.add(url)
            request = self._registry_request(url, "GET")
            try:
                with _OPENER.open(request, timeout=20) as response:
                    data: object = json.loads(response.read())
                    link = response.headers.get("Link")
            except HTTPError as error:
                raise _http_error(path, error) from error
            except (URLError, OSError, ValueError) as error:
                raise FlyApiError(path, detail="tag list could not be read") from error
            if not isinstance(data, dict):
                raise FlyApiError(path, detail="registry returned an invalid tag list")
            raw_tags = cast(Mapping[str, object], data).get("tags")
            if raw_tags is not None and not isinstance(raw_tags, list):
                raise FlyApiError(path, detail="registry returned an invalid tag list")
            name = cast(Mapping[str, object], data).get("name")
            if name is not None and not isinstance(name, str):
                raise FlyApiError(path, detail="registry returned an invalid tag list")
            if listed_name is None:
                listed_name = name
            elif name != listed_name:
                raise FlyApiError(path, detail="registry changed repository between pages")
            values = cast(list[object], raw_tags) if raw_tags is not None else []
            tags.extend(tag for tag in values if isinstance(tag, str))
            match = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            if not match:
                return tags
            next_url = urljoin(url, match.group(1))
            base = urlsplit(self.registry_base_url)
            next_page = urlsplit(next_url)
            next_repo = re.fullmatch(r"/v2/([^/]+)/tags/list", next_page.path)
            if (
                (next_page.scheme, next_page.netloc) != (base.scheme, base.netloc)
                or next_repo is None
                or next_repo.group(1) not in {repo, listed_name}
                or next_page.fragment
            ):
                raise FlyApiError(path, detail="registry returned an unsafe next page")
            url = next_url

    def delete_manifest(self, repository: str, digest: str) -> bool:
        if not DIGEST_PATTERN.fullmatch(digest):
            raise ValueError("registry deletion requires a SHA-256 digest")
        repo = repository.removeprefix("registry.fly.io/")
        path = f"/v2/{repo}/manifests/{digest}"
        request = self._registry_request(f"{self.registry_base_url}{path}", "DELETE")
        try:
            with _OPENER.open(request, timeout=20) as response:
                return response.status in (200, 202)
        except HTTPError as error:
            if error.code == 404:
                return False
            raise _http_error(path, error) from error
        except (URLError, OSError) as error:
            raise FlyApiError(path, detail="manifest deletion could not be completed") from error


def _http_error(path: str, error: HTTPError) -> FlyApiError:
    reason = _http_reason(error)
    detail = f"HTTP {error.code}: {reason}" if reason else f"HTTP {error.code}"
    return FlyApiError(path, error.code, detail, reason)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FlyApiError("response", detail="API returned an unexpected response")
    return cast(Mapping[str, object], value)
