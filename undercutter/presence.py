"""Optional: show the seller as online, the way an open bhopp.com tab does.

socket.io v4 on the API host, namespace /user, auth {token}, `presence:ping`
every 60 s. Reconnects by itself and never raises. python-socketio is only
imported when this is switched on.
"""
from __future__ import annotations

import asyncio
import logging

from .bhopp import BASE

log = logging.getLogger("undercutter")
NAMESPACE = "/user"
PING_SECONDS = 60
RETRY_SECONDS = 30


def _socket():
    import socketio
    return socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)


class Presence:
    def __init__(self, token: str, wanted=lambda: True, socket_factory=_socket, url: str = BASE):
        self.token = token
        self.wanted = wanted
        self.socket_factory = socket_factory
        self.url = url
        self.socket = None
        self.pings = 0

    async def close(self) -> None:
        socket, self.socket = self.socket, None
        if socket is not None:
            try:
                await socket.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def tick(self) -> None:
        if not self.wanted():
            await self.close()
            return
        if self.socket is not None and not self.socket.connected:
            await self.close()
        try:
            if self.socket is None:
                socket = self.socket_factory()
                await socket.connect(self.url, namespaces=[NAMESPACE], auth={"token": self.token},
                                     transports=["websocket", "polling"], wait_timeout=15)
                self.socket = socket
                log.info("always online: connected")
            await self.socket.emit("presence:ping", namespace=NAMESPACE)
            self.pings += 1
        except Exception as e:  # noqa: BLE001
            log.warning("always online: %s, retrying", type(e).__name__)
            await self.close()

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(PING_SECONDS if self.socket is not None else RETRY_SECONDS)
