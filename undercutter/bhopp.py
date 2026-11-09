"""The few BHOPP calls this tool needs (unofficial API). Prices are in cents."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

BASE = "https://api.sublimelistings.com:5000"
USER_AGENT = "lobster-undercutter"
MIN_REQUEST_GAP = 1.0
MAX_RETRY_AFTER = 900.0
DEFAULT_RETRY_AFTER = 60.0


class BhoppError(Exception):
    pass


class AuthError(BhoppError):
    """No token, or BHOPP rejected it."""


class RateLimited(BhoppError):
    def __init__(self, retry_after: float, status: int):
        super().__init__(f"HTTP {status}, waiting {retry_after:.0f}s")
        self.retry_after = retry_after


class TemporaryError(BhoppError):
    """Timeouts, connection failures, 5xx."""


class Refused(BhoppError):
    """BHOPP answered, but said no or sent something unusable."""


class WritesDisabled(BhoppError):
    """A price change was attempted on a client not allowed to write."""


def token_claims(token: str) -> dict:
    """The JWT's payload, not verified. Empty if it cannot be read."""
    try:
        body = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError):
        return {}


def token_expiry(token: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(token_claims(token)["exp"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


def token_seller_id(token: str) -> str | None:
    user = token_claims(token).get("user")
    return str(user) if user else None


def _retry_after(value: str | None) -> float:
    try:
        return max(1.0, min(MAX_RETRY_AFTER, float(value)))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def _site_error(text: str) -> tuple[bool, str]:
    """(is it BHOPP's own JSON error, its words on one short line)."""
    try:
        data = json.loads(text)
    except ValueError:
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    for key in ("errors", "message", "error"):
        value = data.get(key)
        if isinstance(value, list):
            value = "; ".join(map(str, value))
        if value:
            return True, " ".join(str(value).split())[:200]
    return False, ""


class Client:
    def __init__(self, token: str = "", *, allow_writes: bool = False, base: str = BASE,
                 min_gap: float = MIN_REQUEST_GAP):
        self._token = token
        self._allow_writes = allow_writes
        self._base = base.rstrip("/")
        self._min_gap = min_gap
        self._next_at = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(self, method: str, path: str, *, auth: bool, body: dict | None = None) -> dict:
        headers = {}
        if auth:
            if not self._token:
                raise AuthError("no BHOPP token")
            headers["Authorization"] = f"Bearer {self._token}"
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_at = time.monotonic() + self._min_gap
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=20))
        try:
            # Redirects are never followed, so the token stays on this host.
            async with self._session.request(method, self._base + path, json=body, headers=headers,
                                             allow_redirects=False) as resp:
                status = resp.status
                retry_after = resp.headers.get("Retry-After")
                text = await resp.text()
        except asyncio.TimeoutError:
            raise TemporaryError("timeout") from None
        except aiohttp.ClientError as e:
            raise TemporaryError(f"connection problem: {type(e).__name__}") from None

        is_site_error, reason = _site_error(text)
        if status == 401:
            raise AuthError("BHOPP rejected the token (HTTP 401)")
        if status == 429 or (status == 403 and not is_site_error):
            raise RateLimited(_retry_after(retry_after), status)
        if status >= 500:
            raise TemporaryError(f"HTTP {status}")
        if status >= 300:
            raise Refused(f"HTTP {status}" + (f": {reason}" if reason else ""))
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            raise Refused(f"non-JSON answer (HTTP {status})") from None
        if not isinstance(data, dict):
            raise Refused(f"unexpected answer (HTTP {status})")
        return data

    async def my_offers(self) -> list[dict]:
        offers = (await self._request("GET", "/seller/offers", auth=True)).get("offers")
        if not isinstance(offers, list):
            raise Refused("/seller/offers did not return a list")
        return offers

    async def product_offers(self, slug: str) -> list[dict]:
        path = f"/products/{quote(slug, safe='')}/offers"
        offers = (await self._request("GET", path, auth=False)).get("offers")
        if not isinstance(offers, list):
            raise Refused(f"{path} did not return a list")
        return offers

    async def set_price(self, offer_id: str, amount: int) -> dict:
        if not self._allow_writes:
            raise WritesDisabled("this client is read-only (dry run)")
        path = f"/seller/offers/{quote(offer_id, safe='')}"
        data = await self._request("PATCH", path, auth=True, body={"amount": int(amount)})
        offer = data.get("offer")
        return offer if isinstance(offer, dict) else {}
