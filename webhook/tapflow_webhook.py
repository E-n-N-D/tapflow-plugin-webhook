"""
Webhook, a TapFlow Plugin: its one Action Kind, `tapflow.webhook.send`, sends an HTTP request.

It uses only the standard library: http.client, so redirects aren't followed and
no proxy is used. A 2xx answer ends the run Done. Any other status, a redirect
included, ends it Failed with that status, unless `fail_on_error` is off. A URL
that isn't http(s), a network error and no answer within `timeout_s` of the
run's start end it Failed too. A cancel ends it at once: one heard before the
connection is up means the request is never sent. The log and the phone name
only the URL's host, since a webhook's URL often holds its secret.
"""

import http.client
import string
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from tapflow_plugin import ActionFailed, Context, action

# Keep in step with the manifest's version.
USER_AGENT = "TapFlow-Webhook/1.0.0"

# What quote() leaves as it is in a URL's path and query: ASCII punctuation, so the URL's own
# syntax and any %-escapes in it stay. Spaces, control and non-ASCII characters are escaped.
_URL_SAFE = string.punctuation


@dataclass(frozen=True)
class _Request:
    """A run's params, checked, as the request they make."""

    https: bool
    host: str
    port: int | None
    target: str
    method: str
    body: bytes | None
    headers: dict[str, str]
    fail_on_error: bool
    timeout_s: float

    @property
    def where(self) -> str:
        """The host and port, as messages name them: never the path or query."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}" if self.port else host

    def too_slow(self) -> ActionFailed:
        unit = "second" if self.timeout_s == 1 else "seconds"
        return ActionFailed(f"{self.where} didn't answer within {self.timeout_s:g} {unit}")


@action("send")
def send(params: dict[str, Any], ctx: Context) -> None:
    request = _request(params)
    deadline = time.monotonic() + request.timeout_s
    # A connect or a name lookup can't be interrupted, so the request runs on a thread of its
    # own: a cancel or the deadline stops waiting for it at once. The thread gives up by the
    # deadline, but for a name lookup, which ends when it ends.
    finished = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            _send(request, deadline, ctx)
        except BaseException as exc:
            failure.append(exc)
        finally:
            finished.set()

    threading.Thread(target=run, name="webhook send", daemon=True).start()
    while not finished.wait(0.05):
        if ctx.cancelled:
            return
        if time.monotonic() >= deadline:
            raise request.too_slow()
    if failure:
        raise failure[0]


def _request(params: dict[str, Any]) -> _Request:
    """The request `params` make, or ActionFailed saying what's wrong with them."""
    try:
        parts = urllib.parse.urlsplit(params["url"])
        port = parts.port
    except ValueError:
        raise ActionFailed("The URL isn't valid") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ActionFailed("The URL must be an http:// or https:// address")
    if parts.username is not None or parts.password is not None:
        raise ActionFailed("The URL can't hold a user name or password")
    if any(ch.isspace() or not ch.isprintable() for ch in parts.hostname):
        raise ActionFailed("The URL isn't valid")

    target = urllib.parse.quote(parts.path or "/", safe=_URL_SAFE)
    if parts.query:
        target += "?" + urllib.parse.quote(parts.query, safe=_URL_SAFE)
    body = params["body"].encode("utf-8") if params["body"] else None
    headers = {"User-Agent": USER_AGENT}
    if body is not None and params["content_type"]:
        headers["Content-Type"] = _header_value(params["content_type"])
    return _Request(
        https=parts.scheme.lower() == "https",
        host=parts.hostname,
        port=port,
        target=target,
        method=params["method"],
        body=body,
        headers=headers,
        fail_on_error=params["fail_on_error"],
        timeout_s=params["timeout_s"],
    )


def _header_value(value: str) -> str:
    """`value` if a header can hold it: Latin-1, with no control characters."""
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        raise ActionFailed("The content type isn't valid") from None
    if not value.isprintable():
        raise ActionFailed("The content type isn't valid")
    return value


def _send(request: _Request, deadline: float, ctx: Context) -> None:
    connection_type = http.client.HTTPSConnection if request.https else http.client.HTTPConnection
    connection = connection_type(request.host, request.port, timeout=_left(request, deadline))
    try:
        try:
            connection.connect()
        except TimeoutError:
            raise request.too_slow() from None
        except OSError as exc:
            raise ActionFailed(f"Couldn't connect to {request.where}: {_why(exc)}") from None
        # A cancel heard while connecting means the request is never sent.
        if ctx.cancelled:
            return
        assert connection.sock is not None
        connection.sock.settimeout(_left(request, deadline))
        try:
            connection.request(
                request.method, request.target, body=request.body, headers=request.headers
            )
            response = connection.getresponse()
        except TimeoutError:
            raise request.too_slow() from None
        # Hung up, or answered with something that isn't HTTP.
        except (http.client.HTTPException, OSError) as exc:
            raise ActionFailed(f"{request.where} didn't answer: {_why(exc)}") from None
    finally:
        connection.close()

    status = f"{response.status} {response.reason}".strip()
    ctx.log.info("%s to %s answered %s", request.method, request.where, status)
    if not 200 <= response.status < 300 and request.fail_on_error:
        raise ActionFailed(f"The server answered {status}")


def _left(request: _Request, deadline: float) -> float:
    """The seconds left until `deadline`, or ActionFailed once it has passed."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise request.too_slow()
    return left


def _why(exc: OSError | http.client.HTTPException) -> str:
    """An error's reason as people read it: an OSError's own words, without its error number."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__
