"""Startup, token warnings, redaction, presence."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import undercutter.__main__ as app
from undercutter.config import Settings
from undercutter.presence import NAMESPACE, Presence

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiczEifQ.abc-DEF_123"


def test_redaction():
    formatter = app.RedactingFormatter("%(message)s", "plain-secret")
    message = f"got plain-secret and {JWT} in a failure"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, message, None, None)
    text = formatter.format(record)
    assert "plain-secret" not in text and JWT not in text and text.count("***") == 2


def watch_with(hours_left):
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    watch = app.TokenWatch("", now=lambda: now)
    watch.expiry = now + timedelta(hours=hours_left)
    return watch


def test_token_warning(caplog):
    with caplog.at_level(logging.WARNING, "undercutter"):
        watch_with(48).check()
        assert caplog.text == ""
        watch = watch_with(5)
        watch.check()
        watch.check()
    assert caplog.text.count("expires in 5.0 hours") == 1


def test_expired_token_does_no_requests():
    class Blocked:
        def __init__(self, *a, **k):
            pass

        async def my_offers(self):
            raise AssertionError("must not call BHOPP with a dead token")

        async def close(self):
            pass

    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1000, "user": "s1"}).encode()).decode().rstrip("=")
    original = app.Client
    app.Client = Blocked
    try:
        assert asyncio.run(app.run(Settings(token=f"h.{payload}.s"), once=True)) == 1
    finally:
        app.Client = original


def test_dry_run_builds_a_read_only_client():
    built = {}

    class Recording:
        def __init__(self, token, allow_writes):
            built.update(allow_writes=allow_writes)

        async def my_offers(self):
            return []

        async def close(self):
            pass

    original = app.Client
    app.Client = Recording
    try:
        assert asyncio.run(app.run(Settings(token="a.b.c", dry_run=True), once=True)) == 0
    finally:
        app.Client = original
    assert built == {"allow_writes": False}


class FakeSocket:
    def __init__(self, fail_connect=False):
        self.connected = False
        self.fail_connect = fail_connect
        self.emitted = []
        self.connect_kw = None

    async def connect(self, url, **kw):
        if self.fail_connect:
            raise ConnectionError("refused")
        self.connected = True
        self.connect_kw = (url, kw)

    async def emit(self, event, namespace):
        self.emitted.append((event, namespace))

    async def disconnect(self):
        self.connected = False


def test_presence_connects_and_pings():
    socket = FakeSocket()
    presence = Presence("tok", socket_factory=lambda: socket)

    async def go():
        await presence.tick()
        await presence.tick()

    asyncio.run(go())
    assert socket.connect_kw[1]["namespaces"] == [NAMESPACE]
    assert socket.connect_kw[1]["auth"] == {"token": "tok"}
    assert socket.emitted == [("presence:ping", NAMESPACE)] * 2


def test_presence_reconnects_and_survives_failure():
    sockets = [FakeSocket(fail_connect=True), FakeSocket(), FakeSocket()]
    made = iter(sockets)
    presence = Presence("tok", socket_factory=lambda: next(made))

    async def go():
        await presence.tick()                      # connect fails, no exception
        assert presence.socket is None
        await presence.tick()
        sockets[1].connected = False               # the connection drops
        await presence.tick()

    asyncio.run(go())
    assert presence.socket is sockets[2] and presence.pings == 2


def test_presence_stops_when_not_wanted():
    socket = FakeSocket()
    wanted = [True]
    presence = Presence("tok", wanted=lambda: wanted[0], socket_factory=lambda: socket)

    async def go():
        await presence.tick()
        wanted[0] = False
        await presence.tick()

    asyncio.run(go())
    assert presence.socket is None and socket.connected is False
