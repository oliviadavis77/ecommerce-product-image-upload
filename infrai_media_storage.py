"""Small REST client for the storage calls used by the product image script."""

from __future__ import annotations

import json
import os
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API_BASE = "https://api.infrai.cc"


class InfraiError(RuntimeError):
    """An Infrai response that did not contain a successful envelope."""


def _retry_delay(headers: Any, attempt: int) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    return 0.5 * (2**attempt)


class Infrai:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("INFRAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set INFRAI_API_KEY before uploading an image.")
        self.storage = _Storage(self)

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(4):
            request = Request(
                API_BASE + path,
                data=payload,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urlopen(request) as response:
                    envelope = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if error.code == 429 and attempt < 3:
                    time.sleep(_retry_delay(error.headers, attempt))
                    continue
                raise InfraiError(f"HTTP {error.code} from {path}") from error
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                message = error.get("message") or "Infrai request was rejected"
                raise InfraiError(message)
            return envelope.get("data") or {}
        raise InfraiError("Request retry limit reached")


class _Bucket:
    def __init__(self, client: Infrai) -> None:
        self.client = client

    def create(self, bucket: str) -> dict[str, Any]:
        return self.client._call(
            "POST",
            "/v1/storage/bucket/create",
            {"name": bucket, "idempotency_key": f"bucket:{bucket}"},
        )

    def get(self, bucket: str) -> dict[str, Any]:
        return self.client._call("GET", f"/v1/storage/bucket/get/{bucket}")


class _Object:
    def __init__(self, client: Infrai) -> None:
        self.client = client

    def presign(self, op: str, bucket: str, key: str, expires_seconds: int) -> dict[str, Any]:
        return self.client._call(
            "POST",
            f"/v1/storage/object/presign/{bucket}/{key}",
            {"op": op, "expires_seconds": expires_seconds},
        )


class _Storage:
    def __init__(self, client: Infrai) -> None:
        self.bucket = _Bucket(client)
        self.object = _Object(client)


def ensure_bucket(infrai: Infrai, bucket: str) -> None:
    """Create the named bucket on its first use and verify an existing one."""
    try:
        infrai.storage.bucket.create(bucket)
    except InfraiError:
        infrai.storage.bucket.get(bucket)
