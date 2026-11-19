"""HTTP for the witness services, with a timeout on every call.

Tests replace resolve() to send requests for https://rekor.example and the like
to local fake servers; nothing else changes.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

from bgl.errors import WitnessError

# [DECISION] Every witness request gives up after 15 seconds. A slow service
# must not hold a run back for long: the open is then simply unwitnessed.
TIMEOUT: Final[float] = 15.0
MAX_RESPONSE: Final[int] = 10_000_000
USER_AGENT: Final[str] = "bgl"


def resolve(url: str) -> str:
    """The address actually requested for url (identity outside tests)."""
    return url


def join(base: str, path: str) -> str:
    return resolve(base.rstrip("/") + "/" + path.lstrip("/"))


def request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    accept: str = "application/json",
    timeout: float = TIMEOUT,
) -> bytes:
    """One HTTP request. Raises WitnessError on any failure."""
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if content_type is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data: bytes = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", "replace").strip()
        host = urllib.parse.urlsplit(url).netloc
        raise WitnessError(f"{host} answered HTTP {error.code}: {detail}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        host = urllib.parse.urlsplit(url).netloc
        reason = getattr(error, "reason", error)
        raise WitnessError(f"cannot reach {host}: {reason}") from None
    if len(data) > MAX_RESPONSE:
        raise WitnessError(f"{url} sent more than {MAX_RESPONSE} bytes")
    return data


def request_json(method: str, url: str, payload: Any = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    data = request(
        method,
        url,
        body=body,
        content_type=None if body is None else "application/json",
    )
    try:
        return json.loads(data)
    except ValueError:
        raise WitnessError(f"{url} did not return JSON") from None
