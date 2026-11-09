"""Run with `python -m undercutter`. See README.md."""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from .bhopp import AuthError, BhoppError, Client, RateLimited, token_expiry
from .config import ConfigError, Settings, load_settings
from .engine import Undercutter, summary
from .presence import Presence

log = logging.getLogger("undercutter")
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
WARN_BEFORE = timedelta(hours=24)
HEARTBEAT_SECONDS = 3600


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secret: str = ""):
        super().__init__(fmt)
        self.secret = secret

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.secret:
            text = text.replace(self.secret, "***")
        return JWT.sub("***", text)


def setup_logging(token: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", token))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


class TokenWatch:
    """Warns before the 7-day token runs out, at most hourly."""

    def __init__(self, token: str, now=lambda: datetime.now(timezone.utc)):
        self.expiry = token_expiry(token)
        self.now = now
        self._last_warned = None

    def expired(self) -> bool:
        return self.expiry is not None and self.now() >= self.expiry

    def check(self) -> None:
        if self.expiry is None:
            if self._last_warned is None:
                self._last_warned = self.now()
                log.warning("can't read the token's expiry, is BHOPP_TOKEN a full token?")
            return
        left = self.expiry - self.now()
        if left > WARN_BEFORE:
            return
        if self._last_warned is not None and self.now() - self._last_warned < timedelta(hours=1):
            return
        self._last_warned = self.now()
        if left <= timedelta(0):
            log.error("BHOPP_TOKEN has expired, paste a new one. Doing nothing until then.")
        else:
            log.warning("BHOPP_TOKEN expires in %.1f hours, paste a new one soon.", left.total_seconds() / 3600)


async def run(settings: Settings, once: bool) -> int:
    client = Client(settings.token, allow_writes=not settings.dry_run)
    undercutter = Undercutter(client, settings)
    watch = TokenWatch(settings.token)
    presence = Presence(settings.token, wanted=lambda: not watch.expired())
    presence_task = asyncio.create_task(presence.run()) if settings.always_online and not once else None
    log.info("%s, every %ds, step %dc, %d item rules, global floor %s, always online %s",
             "DRY RUN (no prices will change)" if settings.dry_run else "LIVE", settings.poll_seconds, settings.step,
             len(settings.rules), "none" if settings.global_floor is None else f"{settings.global_floor}c",
             "on" if presence_task else "off")
    last_line, last_at, status = None, 0.0, 0
    try:
        while True:
            delay = settings.poll_seconds
            status = 0
            watch.check()
            if watch.expired():
                status = 1
            else:
                try:
                    line = summary(await undercutter.cycle())
                    if line != last_line or time.monotonic() - last_at >= HEARTBEAT_SECONDS:
                        log.info("cycle: %s", line)
                        last_line, last_at = line, time.monotonic()
                except RateLimited as e:
                    log.warning("BHOPP is limiting us: %s", e)
                    delay = max(delay, e.retry_after)
                    status = 1
                except AuthError as e:
                    log.error("%s, paste a new BHOPP_TOKEN", e)
                    status = 1
                except BhoppError as e:
                    log.warning("this pass failed: %s", e)
                    status = 1
                except Exception:  # noqa: BLE001
                    log.exception("unexpected problem, trying again next pass")
                    status = 1
            if once:
                return status
            await asyncio.sleep(delay)
    finally:
        if presence_task:
            presence_task.cancel()
            await asyncio.gather(presence_task, return_exceptions=True)
        await presence.close()
        await client.close()


async def preview(settings: Settings, seller: str, slugs: list[str]) -> int:
    """No login needed: run the real decision on one seller's public offers."""
    client = Client()
    undercutter = Undercutter(client, dataclasses.replace(settings, token="", dry_run=True, state_file=""))
    offers = []
    try:
        for slug in slugs:
            for offer in await client.product_offers(slug):
                if str((offer.get("seller") or {}).get("username", "")).casefold() == seller.casefold():
                    offers.append({**offer, "product": {"slug": slug, "name": slug}, "state": "active"})
        log.info("preview: %d public offers from %s", len(offers), seller)
        log.info("cycle: %s", summary(await undercutter.process(offers)))
        return 0
    except BhoppError as e:
        log.error("preview failed: %s", e)
        return 1
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="undercutter", description="Keep your BHOPP offers the cheapest.")
    parser.add_argument("--once", action="store_true", help="do one pass and exit")
    commands = parser.add_subparsers(dest="command")
    p = commands.add_parser("preview", help="dry run on a seller's public offers, no token needed")
    p.add_argument("seller", help="BHOPP username")
    p.add_argument("slugs", nargs="+", help="product slugs to look at")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return 2
    setup_logging(settings.token)
    if args.command == "preview":
        return asyncio.run(preview(settings, args.seller, args.slugs))
    if not settings.token:
        print("BHOPP_TOKEN is not set, see README.md", file=sys.stderr)
        return 2
    try:
        return asyncio.run(run(settings, args.once))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
