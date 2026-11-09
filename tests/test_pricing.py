import pytest

from undercutter.pricing import (AT_CEILING, AT_FLOOR, BIG_DROP, LOWER, LOWER_TO_FLOOR, NO_RIVALS, ON_TARGET, RAISE,
                                 RAISE_TO_CEILING, RAISE_TO_FLOOR, decide, rival_prices)


@pytest.mark.parametrize("current,rivals,floor,step,status,new", [
    (600, [550, 700], 400, 1, LOWER, 549),                # undercut the cheapest
    (550, [550], 400, 1, LOWER, 549),                     # a tie is not cheapest
    (600, [550], 400, 5, LOWER, 545),                     # the step applies
    (549, [550], 400, 1, ON_TARGET, None),                # exactly one step under: nothing to do
    (500, [550], 400, 1, RAISE, 549),                     # too far under: go back up
    (549, [700, 620], 400, 1, RAISE, 619),                # the cheapest sold, next is higher
    (600, [300], 400, 1, LOWER_TO_FLOOR, 400),            # would cross the floor: stop at it
    (600, [400], 400, 1, LOWER_TO_FLOOR, 400),            # floor equals the cheapest other
    (600, [401], 400, 1, LOWER, 400),                     # landing exactly on the floor is normal
    (400, [300], 400, 1, AT_FLOOR, None),                 # already holding at the floor
    (350, [300], 400, 1, RAISE_TO_FLOOR, 400),            # floor was raised above the price
    (350, [380], 400, 1, RAISE_TO_FLOOR, 400),
    (600, [], 400, 1, NO_RIVALS, None),                   # no reference price, never guess
    (499, [500], 498, 5, LOWER_TO_FLOOR, 498),            # as close to one step under as the floor allows
])
def test_decide(current, rivals, floor, step, status, new):
    d = decide(current, rivals, floor, step)
    assert (d.status, d.new_amount) == (status, new)


@pytest.mark.parametrize("current,rivals,floor,ceiling,status,new", [
    (549, [900], 400, 700, RAISE_TO_CEILING, 700),        # would go above the original
    (700, [900], 400, 700, AT_CEILING, None),             # holding there
    (549, [700], 400, 700, RAISE, 699),                   # just under the original is fine
    (549, [701], 400, 700, RAISE, 700),                   # one step under equals the original
    (350, [900], 400, 300, RAISE_TO_FLOOR, 400),          # the floor wins over the ceiling
])
def test_ceiling(current, rivals, floor, ceiling, status, new):
    d = decide(current, rivals, floor, 1, ceiling=ceiling)
    assert (d.status, d.new_amount) == (status, new)


@pytest.mark.parametrize("rivals,floor", [([1], 1), ([2], 500), ([499], 500), ([10_000], 1)])
def test_never_below_floor(rivals, floor):
    for current in (1, floor, floor + 1, 20_000):
        new = decide(current, rivals, floor, 3).new_amount
        assert new is None or new >= floor


def test_big_drop_guard():
    assert decide(1000, [500], 100, 1, max_drop_pct=25).status == BIG_DROP
    assert decide(1000, [500], 100, 1, max_drop_pct=25).new_amount is None
    assert decide(1000, [760], 100, 1, max_drop_pct=25).new_amount == 759
    assert decide(1000, [500], 100, 1, max_drop_pct=None).new_amount == 499


def test_rival_filter():
    offers = [
        {"id": "mine", "amount": 10, "currency": "USD", "quantity": 1, "seller": {"id": "other"}},
        {"id": "a", "amount": 20, "currency": "USD", "quantity": 1, "seller": {"id": "me"}},
        {"id": "b", "amount": 30, "currency": "EUR", "quantity": 1, "seller": {"id": "x"}},
        {"id": "c", "amount": 40, "quantity": 1, "seller": {"id": "x"}},
        {"id": "d", "amount": 50, "currency": "USD", "quantity": 0, "seller": {"id": "x"}},
        {"id": "e", "amount": "60", "currency": "USD", "quantity": 1, "seller": {"id": "x"}},
        {"id": "f", "amount": True, "currency": "USD", "quantity": 1, "seller": {"id": "x"}},
        {"id": "g", "amount": 70, "currency": "USD", "quantity": 2, "seller": {"id": "x"}},
        "junk",
    ]
    assert rival_prices(offers, {"mine"}, "me") == [70]
