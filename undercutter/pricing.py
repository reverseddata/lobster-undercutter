"""The pricing decision. Pure functions, all money in CENTS.

The rule: always exactly one step under the cheapest other seller, moving up
as well as down, never below the floor and never above the original price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

LOWER = "lower"
LOWER_TO_FLOOR = "lower_to_floor"
RAISE = "raise"
RAISE_TO_FLOOR = "raise_to_floor"
RAISE_TO_CEILING = "raise_to_ceiling"
ON_TARGET = "on_target"
AT_FLOOR = "at_floor"
AT_CEILING = "at_ceiling"
NO_RIVALS = "no_rivals"
BIG_DROP = "big_drop"


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    new_amount: int | None = None     # None: leave the price alone


def usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


def valid_amount(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def rival_prices(offers: Iterable[dict], own_offer_ids: set[str], seller_id: str | None) -> list[int]:
    """Prices of other sellers' in-stock USD offers."""
    prices = []
    for offer in offers:
        if not isinstance(offer, dict) or str(offer.get("id")) in own_offer_ids:
            continue
        seller = offer.get("seller")
        if seller_id and isinstance(seller, dict) and seller.get("id") == seller_id:
            continue
        amount, quantity = offer.get("amount"), offer.get("quantity")
        if offer.get("currency") == "USD" and valid_amount(amount) and valid_amount(quantity):
            prices.append(amount)
    return prices


def decide(current: int, rivals: list[int], floor: int, step: int, *,
           ceiling: int | None = None, max_drop_pct: int | None = None) -> Decision:
    """What to do with one offer. Never returns a price below `floor`, which
    wins if it is ever above `ceiling` (the original price, the most a raise can reach).

    `max_drop_pct` refuses one drop that big (a troll listing, say) and is
    only passed for items that rely on the flat global floor.
    """
    if not rivals:
        return Decision(NO_RIVALS, "no other sellers, leaving it")
    lowest = min(rivals)
    target = lowest - step
    capped = ceiling is not None and target > ceiling
    if capped:
        target = ceiling
    floored = target < floor
    if floored:
        target, capped = floor, False

    if target == current:
        if floored:
            return Decision(AT_FLOOR, f"holding at floor {usd(floor)}, cheapest other is {usd(lowest)}")
        if capped:
            return Decision(AT_CEILING, f"holding at original price, cheapest other is {usd(lowest)}")
        return Decision(ON_TARGET, f"already {step}c under {usd(lowest)}")
    if target > current:
        if floored:
            return Decision(RAISE_TO_FLOOR, f"below your floor {usd(floor)}, cheapest other is {usd(lowest)}", target)
        if capped:
            return Decision(RAISE_TO_CEILING, f"back to original price, cheapest other is {usd(lowest)}", target)
        return Decision(RAISE, f"cheapest other went up to {usd(lowest)}", target)
    if max_drop_pct and (current - target) * 100 > max_drop_pct * current:
        return Decision(BIG_DROP, f"would drop more than {max_drop_pct}% to {usd(target)} "
                                  f"(cheapest other {usd(lowest)}), set a floor for this item to allow it")
    if floored:
        return Decision(LOWER_TO_FLOOR, f"stopping at floor {usd(floor)}, cheapest other is {usd(lowest)}", target)
    return Decision(LOWER, f"undercutting {usd(lowest)}", target)
