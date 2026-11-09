"""The real Client against a local fake BHOPP over HTTP."""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from undercutter.bhopp import (AuthError, Client, RateLimited, Refused, TemporaryError, WritesDisabled, token_expiry,
                               token_seller_id)


def run_against(handler, scenario, **client_kw):
    async def main():
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        client = Client("tok.en.x", base=str(server.make_url("")), min_gap=0, **client_kw)
        try:
            return await scenario(client)
        finally:
            await client.close()
            await server.close()
    return asyncio.run(main())


def test_patch_shape():
    seen = {}

    async def handler(request):
        seen.update(method=request.method, path=request.path, auth=request.headers.get("Authorization"),
                    body=await request.json())
        return web.json_response({"offer": {"id": "o/1", "amount": 549}})

    async def scenario(client):
        return await client.set_price("o1", 549)

    result = run_against(handler, scenario, allow_writes=True)
    assert result == {"id": "o/1", "amount": 549}
    assert seen == {"method": "PATCH", "path": "/seller/offers/o1", "auth": "Bearer tok.en.x", "body": {"amount": 549}}


def test_read_only_client_sends_nothing():
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.json_response({})

    async def scenario(client):
        with pytest.raises(WritesDisabled):
            await client.set_price("o1", 549)

    run_against(handler, scenario)
    assert calls == []


def test_public_read_has_no_token():
    seen = {}

    async def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.path
        return web.json_response({"offers": [{"id": "a"}]})

    async def scenario(client):
        return await client.product_offers("harvester")

    assert run_against(handler, scenario) == [{"id": "a"}]
    assert seen == {"auth": None, "path": "/products/harvester/offers"}


def test_my_offers_uses_token():
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer tok.en.x"
        return web.json_response({"offers": []})

    async def scenario(client):
        return await client.my_offers()

    assert run_against(handler, scenario) == []


@pytest.mark.parametrize("status,body,headers,expected", [
    (401, {"errors": ["Authentication required."]}, {}, AuthError),
    (429, {}, {"Retry-After": "7"}, RateLimited),
    (403, None, {}, RateLimited),                                   # a bare block page
    (403, {"errors": ["Not your offer"]}, {}, Refused),
    (400, {"errors": ["Bad amount"]}, {}, Refused),
    (500, {}, {}, TemporaryError),
    (302, {}, {"Location": "https://example.invalid/"}, Refused),   # redirects never followed
])
def test_error_mapping(status, body, headers, expected):
    async def handler(request):
        text = "<html>blocked</html>" if body is None else json.dumps(body)
        return web.Response(status=status, text=text, headers=headers)

    async def scenario(client):
        with pytest.raises(expected) as caught:
            await client.my_offers()
        return caught.value

    error = run_against(handler, scenario)
    if status == 429:
        assert error.retry_after == 7
    assert "tok.en.x" not in str(error)


@pytest.mark.parametrize("text", ["not json", "[1]", '{"offers": "x"}'])
def test_unusable_answers(text):
    async def handler(request):
        return web.Response(text=text)

    async def scenario(client):
        with pytest.raises(Refused):
            await client.product_offers("harvester")

    run_against(handler, scenario)


def test_no_token_means_no_seller_calls():
    async def scenario():
        with pytest.raises(AuthError):
            await Client().my_offers()
    asyncio.run(scenario())


def test_token_helpers():
    exp = datetime.now(timezone.utc) + timedelta(days=2)
    payload = base64.urlsafe_b64encode(json.dumps({"user": "s1", "exp": int(exp.timestamp())}).encode()).decode().rstrip("=")
    token = f"h.{payload}.sig"
    assert token_seller_id(token) == "s1"
    assert abs((token_expiry(token) - exp).total_seconds()) < 2
    assert token_seller_id("garbage") is None
    assert token_expiry("a.b.c") is None
