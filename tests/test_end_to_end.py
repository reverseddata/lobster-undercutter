"""Everything real except BHOPP, which is a local fake server."""
import asyncio

import socketio
from aiohttp import web
from aiohttp.test_utils import TestServer

from undercutter.bhopp import Client
from undercutter.config import Settings
from undercutter.engine import Undercutter
from undercutter.presence import NAMESPACE, Presence


def fake_bhopp():
    state = {
        "me": {"id": "mine", "amount": 597, "currency": "USD", "quantity": 33, "state": "active", "live": True,
               "away": False, "lowest": 550, "product": {"slug": "harvester", "name": "Harvester"}},
        "patches": [],
    }

    async def my_offers(request):
        assert request.headers["Authorization"].startswith("Bearer ")
        return web.json_response({"offers": [state["me"]]})

    async def market(request):
        return web.json_response({"offers": [
            {"id": "r1", "amount": 550, "currency": "USD", "quantity": 2, "seller": {"id": "s2", "username": "x"}},
            {"id": "mine", "amount": state["me"]["amount"], "currency": "USD", "quantity": 33,
             "seller": {"id": "me", "username": "LobsterShop"}},
        ]})

    async def patch(request):
        body = await request.json()
        state["patches"].append((request.match_info["id"], body))
        state["me"]["amount"] = body["amount"]
        return web.json_response({"offer": state["me"]})

    app = web.Application()
    app.router.add_get("/seller/offers", my_offers)
    app.router.add_get("/products/{slug}/offers", market)
    app.router.add_patch("/seller/offers/{id}", patch)
    return app, state


def test_live_pass_then_nothing_left_to_do():
    async def main():
        app, state = fake_bhopp()
        server = TestServer(app)
        await server.start_server()
        client = Client("a.b.c", allow_writes=True, base=str(server.make_url("")), min_gap=0)
        undercutter = Undercutter(client, Settings(token="a.b.c", dry_run=False,
                                                   rules={"harvester": 400}))
        try:
            first, second = await undercutter.cycle(), await undercutter.cycle()
        finally:
            await client.close()
            await server.close()
        return first, second, state

    first, second, state = asyncio.run(main())
    assert state["patches"] == [("mine", {"amount": 549})]
    assert state["me"]["amount"] == 549
    assert first["changed"] == 1 and second["changed"] == 0 and second["on_target"] == 1


def test_always_online_with_the_real_socketio_client():
    async def main():
        sio = socketio.AsyncServer(async_mode="aiohttp")
        got = {"auth": None, "pings": asyncio.Event()}

        async def on_connect(sid, environ, auth):
            got["auth"] = auth

        async def on_ping(sid):
            got["pings"].set()

        sio.on("connect", on_connect, namespace=NAMESPACE)
        sio.on("presence:ping", on_ping, namespace=NAMESPACE)
        app = web.Application()
        sio.attach(app)
        server = TestServer(app)
        await server.start_server()
        presence = Presence("the-token", url=str(server.make_url("")).rstrip("/"))
        try:
            await presence.tick()
            await asyncio.wait_for(got["pings"].wait(), 5)
            return got["auth"], presence.pings
        finally:
            await presence.close()
            await server.close()

    auth, pings = asyncio.run(main())
    assert auth == {"token": "the-token"}
    assert pings == 1
