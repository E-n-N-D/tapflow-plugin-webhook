"""A real HTTP server on 127.0.0.1 for the tests to send to, and the params a run of `send` gets."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class Received:
    """One request the server got."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Endpoint:
    """The test server: the status it answers with, how long it waits first, and what it got."""

    url: str
    status: int = 200
    delay_s: float = 0.0
    # Close the connection without answering.
    hang_up: bool = False
    # Answer with this Location header, as a redirect does.
    location: str | None = None
    # Send the answer's headers a byte at a time, this many seconds apart.
    trickle_s: float = 0.0
    received: list[Received] = field(default_factory=list)
    # Set to end a delay early, so the server shuts down at once.
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    state: Endpoint

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            state.received.append(
                Received(
                    self.command,
                    self.path,
                    {k.lower(): v for k, v in self.headers.items()},
                    self.rfile.read(length),
                )
            )
            if state.delay_s:
                state.release.wait(state.delay_s)
            if state.hang_up:
                self.close_connection = True
                return
            if state.trickle_s:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                for byte in b"X-Slow: " + b"a" * 1000:
                    if state.release.wait(state.trickle_s):
                        return
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                return
            self.send_response(state.status)
            if state.location is not None:
                self.send_header("Location", state.location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state = Endpoint(f"http://127.0.0.1:{server.server_address[1]}/hook")
    # A short poll, so shutdown() doesn't wait half a second per test.
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    yield state
    state.release.set()
    server.shutdown()
    server.server_close()


def params(url: str, **overrides: Any) -> dict[str, Any]:
    """A run's params, as the agent sends them: every default filled in."""
    return {
        "url": url,
        "method": "POST",
        "body": "",
        "content_type": "application/json",
        "fail_on_error": True,
        "timeout_s": 30,
        **overrides,
    }
