"""One pass over your offers: decide, then change only what needs changing."""
from __future__ import annotations

import logging
import time
from collections import Counter

from .bhopp import Refused, TemporaryError, token_seller_id
from .config import Settings
from .pricing import BIG_DROP, decide, rival_prices, usd, valid_amount
from .state import Ceilings

log = logging.getLogger("undercutter")


class Undercutter:
    def __init__(self, client, settings: Settings, *, clock=time.monotonic):
        self.client = client
        self.settings = settings
        self.clock = clock
        self.seller_id = token_seller_id(settings.token)
        self.ceilings = Ceilings("" if settings.dry_run else settings.state_file)
        self._changed_at: dict[str, float] = {}
        self._notes: dict[str, str] = {}

    def _note(self, offer_id: str, message: str, level: int = logging.INFO) -> None:
        """Log a line per offer only when what we have to say about it changes."""
        if self._notes.get(offer_id) != message:
            self._notes[offer_id] = message
            log.log(level, "%s", message)

    async def cycle(self) -> Counter:
        return await self.process(await self.client.my_offers())

    async def process(self, offers: list[dict]) -> Counter:
        tally: Counter = Counter(offers=len(offers))
        own_ids = {str(o.get("id")) for o in offers if isinstance(o, dict)}
        rivals_by_slug: dict[str, list[int]] = {}
        try:
            for offer in offers:
                try:
                    await self._handle(offer, own_ids, rivals_by_slug, tally)
                except (TemporaryError, Refused) as e:
                    tally["errors"] += 1
                    log.warning("offer %s: %s", offer.get("id") if isinstance(offer, dict) else "?", e)
            if own_ids:                               # an empty answer must never wipe the originals
                self.ceilings.forget_except(own_ids)
        finally:
            self.ceilings.save()
        for offer_id in set(self._notes) - own_ids:
            del self._notes[offer_id]
        return tally

    async def _handle(self, offer, own_ids, rivals_by_slug, tally: Counter) -> None:
        if not isinstance(offer, dict) or not offer.get("id"):
            tally["unreadable"] += 1
            return
        offer_id = str(offer["id"])
        product = offer.get("product")
        slug = product.get("slug") if isinstance(product, dict) else None
        amount = offer.get("amount")
        if not isinstance(slug, str) or not slug or not valid_amount(amount):
            tally["unreadable"] += 1
            self._note(offer_id, f"offer {offer_id}: missing product or price, skipping", logging.WARNING)
            return
        name = str(product.get("name") or slug)
        label = f"{name} at {usd(amount)}"

        if offer.get("currency") != "USD":
            tally["not_usd"] += 1
            self._note(offer_id, f"{name}: not priced in USD, skipping")
            return
        if offer.get("state", "active") != "active" or not valid_amount(offer.get("quantity")):
            tally["not_listed"] += 1
            return

        limit = self.settings.limit_for(slug, name)
        if limit.excluded:
            tally["excluded"] += 1
            self._note(offer_id, f"{label}: excluded")
            return
        if limit.floor is None:
            tally["no_floor"] += 1
            self._note(offer_id, f"{label}: no floor set, not touching it")
            return

        ceiling = self.ceilings.observe(offer_id, amount)
        if slug not in rivals_by_slug:
            rivals_by_slug[slug] = rival_prices(await self.client.product_offers(slug), own_ids, self.seller_id)

        decision = decide(amount, rivals_by_slug[slug], limit.floor, self.settings.step, ceiling=ceiling,
                          max_drop_pct=self.settings.global_max_drop_pct if limit.is_global else None)
        tally[decision.status] += 1
        if decision.new_amount is None:
            level = logging.WARNING if decision.status == BIG_DROP else logging.INFO
            self._note(offer_id, f"{label}: {decision.reason}", level)
            return

        change = f"{name}: {usd(amount)} -> {usd(decision.new_amount)} ({decision.reason})"
        if self.settings.dry_run:
            self._note(offer_id, f"DRY RUN, would change {change}")
            return
        await self._apply(offer_id, name, decision.new_amount, change, tally)

    async def _apply(self, offer_id: str, name: str, target: int, change: str, tally: Counter) -> None:
        now = self.clock()
        last = self._changed_at.get(offer_id)
        if last is not None and now - last < self.settings.cooldown_seconds:
            tally["cooldown"] += 1
            return
        self._changed_at[offer_id] = now          # counts even if it fails: don't hammer a refusal
        self.ceilings.about_to_set(offer_id, target)
        updated = await self.client.set_price(offer_id, target)
        tally["changed"] += 1
        self._notes.pop(offer_id, None)
        log.info("changed %s", change)
        got = updated.get("amount")
        if valid_amount(got) and got != target:
            log.warning("%s: asked for %s but BHOPP says %s", name, usd(target), usd(got))
        self.ceilings.was_set(offer_id, got if valid_amount(got) else target)


def summary(tally: Counter) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(tally.items()))
