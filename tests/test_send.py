"""The `send` action, run as the agent runs it, against a real HTTP server on 127.0.0.1."""

from __future__ import annotations

import socket
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import tapflow_webhook  # noqa: F401: registers its actions
from conftest import params
from tapflow_plugin.testing import Result, run_action

if TYPE_CHECKING:
    from conftest import Endpoint


def test_a_2xx_answer_ends_done(endpoint: Endpoint) -> None:
    endpoint.status = 204

    assert run_action("send", params(endpoint.url)) == Result("done")
    assert len(endpoint.received) == 1


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (404, "The server answered 404 Not Found"),
        (500, "The server answered 500 Internal Server Error"),
    ],
)
def test_an_error_status_ends_failed_with_it(endpoint: Endpoint, status: int, message: str) -> None:
    endpoint.status = status

    assert run_action("send", params(endpoint.url)) == Result("failed", message)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_it_sends_the_method_body_and_content_type(endpoint: Endpoint, method: str) -> None:
    body = '{"scene": "Starting soon", "emoji": "🎬"}'

    result = run_action(
        "send", params(endpoint.url, method=method, body=body, content_type="text/plain")
    )

    assert result == Result("done")
    [received] = endpoint.received
    assert (received.method, received.path) == (method, "/hook")
    assert received.body == body.encode("utf-8")
    assert received.headers["content-type"] == "text/plain"


def test_a_get_without_a_body_sends_none(endpoint: Endpoint) -> None:
    assert run_action("send", params(endpoint.url, method="GET")) == Result("done")

    [received] = endpoint.received
    assert received.method == "GET"
    assert received.body == b""
    assert "content-type" not in received.headers


def test_a_refused_connection_ends_failed() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Nothing listens on the port now.

    result = run_action("send", params(f"http://127.0.0.1:{port}/hook"))

    assert result.outcome == "failed"
    assert result.message is not None
    assert result.message.startswith(f"Couldn't connect to 127.0.0.1:{port}: ")


def test_a_server_that_doesnt_answer_in_time_ends_failed(endpoint: Endpoint) -> None:
    endpoint.delay_s = 10
    host = endpoint.url.removeprefix("http://").removesuffix("/hook")

    result = run_action("send", params(endpoint.url, timeout_s=0.3))

    assert result == Result("failed", f"{host} didn't answer within 0.3 seconds")


def test_a_server_that_hangs_up_without_answering_ends_failed(endpoint: Endpoint) -> None:
    endpoint.hang_up = True
    host = endpoint.url.removeprefix("http://").removesuffix("/hook")

    result = run_action("send", params(endpoint.url))

    assert result.outcome == "failed"
    assert result.message is not None
    assert result.message.startswith(f"{host} didn't answer: ")


def test_a_cancel_ends_it_promptly_while_the_server_is_slow(endpoint: Endpoint) -> None:
    endpoint.delay_s = 10
    started = time.monotonic()

    result = run_action("send", params(endpoint.url), cancel_after=0.2)

    assert result == Result("cancelled")
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize(
    "url",
    ["example.com/hook", "ftp://example.com/hook", "file:///C:/Windows/win.ini", "http:///hook"],
)
def test_a_url_that_isnt_http_ends_failed(url: str) -> None:
    assert run_action("send", params(url)) == Result(
        "failed", "The URL must be an http:// or https:// address"
    )


@pytest.mark.parametrize("url", ["http://example.com:port/hook", "http://example.com:99999/hook"])
def test_a_malformed_url_ends_failed(url: str) -> None:
    assert run_action("send", params(url)) == Result("failed", "The URL isn't valid")


def test_an_error_status_ends_done_when_it_shouldnt_fail(endpoint: Endpoint) -> None:
    endpoint.status = 503

    assert run_action("send", params(endpoint.url, fail_on_error=False)) == Result("done")
    assert len(endpoint.received) == 1


def test_a_redirect_isnt_followed_and_ends_failed_with_its_status(endpoint: Endpoint) -> None:
    endpoint.status = 301
    endpoint.location = endpoint.url + "/moved"

    result = run_action("send", params(endpoint.url, body="{}"))

    assert result == Result("failed", "The server answered 301 Moved Permanently")
    assert [received.path for received in endpoint.received] == ["/hook"]


def test_a_redirect_ends_done_when_it_shouldnt_fail(endpoint: Endpoint) -> None:
    endpoint.status = 302
    endpoint.location = endpoint.url + "/moved"

    assert run_action("send", params(endpoint.url, fail_on_error=False)) == Result("done")
    assert [received.path for received in endpoint.received] == ["/hook"]


def test_a_cancel_while_connecting_ends_it_promptly_and_nothing_is_sent(
    endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A slow name lookup or connect, which a cancel can't cut short.
    connect = socket.create_connection

    def slow_connect(*args: Any, **kwargs: Any) -> socket.socket:
        time.sleep(1)
        return connect(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", slow_connect)
    started = time.monotonic()

    result = run_action("send", params(endpoint.url), cancel_after=0.1)

    assert result == Result("cancelled")
    assert time.monotonic() - started < 0.8
    time.sleep(1.5)  # The connection is up by now.
    assert endpoint.received == []


def test_the_timeout_is_a_deadline_for_the_whole_run(endpoint: Endpoint) -> None:
    # Each byte comes well within the timeout, but the answer never ends.
    endpoint.trickle_s = 0.1
    host = endpoint.url.removeprefix("http://").removesuffix("/hook")
    started = time.monotonic()

    result = run_action("send", params(endpoint.url, timeout_s=0.5))

    assert result == Result("failed", f"{host} didn't answer within 0.5 seconds")
    assert time.monotonic() - started < 1.5


def test_it_names_itself_in_the_user_agent(endpoint: Endpoint) -> None:
    manifest = (Path(tapflow_webhook.__file__).parent / "tapflow-plugin.toml").read_bytes()
    version = tomllib.loads(manifest.decode("utf-8"))["version"]

    assert run_action("send", params(endpoint.url)) == Result("done")
    assert endpoint.received[0].headers["user-agent"] == f"TapFlow-Webhook/{version}"


def test_a_body_with_no_content_type_is_sent_without_one(endpoint: Endpoint) -> None:
    assert run_action("send", params(endpoint.url, body="raw", content_type="")) == Result("done")

    [received] = endpoint.received
    assert received.body == b"raw"
    assert "content-type" not in received.headers


def test_a_url_with_spaces_or_other_characters_is_percent_encoded(endpoint: Endpoint) -> None:
    url = endpoint.url + "/my scene?text=héllo&done=100%25#fragment"

    assert run_action("send", params(url)) == Result("done")
    assert endpoint.received[0].path == "/hook/my%20scene?text=h%C3%A9llo&done=100%25"


@pytest.mark.parametrize(
    "url", ["https://bob:s3cret@example.com/hook", "https://bob@example.com/hook"]
)
def test_a_url_holding_a_user_name_or_password_ends_failed(url: str) -> None:
    assert run_action("send", params(url)) == Result(
        "failed", "The URL can't hold a user name or password"
    )


@pytest.mark.parametrize("content_type", ["text/plain\r\nX-Injected: 1", "text/plain; name=✓"])
def test_a_content_type_a_header_cant_hold_ends_failed(
    endpoint: Endpoint, content_type: str
) -> None:
    result = run_action("send", params(endpoint.url, body="{}", content_type=content_type))

    assert result == Result("failed", "The content type isn't valid")
    assert endpoint.received == []


def test_an_ipv6_host_is_named_in_brackets() -> None:
    with socket.socket(socket.AF_INET6) as sock:
        sock.bind(("::1", 0))
        port = sock.getsockname()[1]

    result = run_action("send", params(f"http://[::1]:{port}/hook"))

    assert result.outcome == "failed"
    assert result.message is not None
    assert result.message.startswith(f"Couldn't connect to [::1]:{port}: ")
