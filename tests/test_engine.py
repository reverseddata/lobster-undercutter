import asyncio
import logging

import pytest

from undercutter.bhopp import AuthError, RateLimited, Refused, TemporaryError
from undercutter.config import Settings
from undercutter.engine import Undercutter


class FakeClient:
    def __init__(self, offers, market=None):
        self.offers = offers
        self.market = market or {}
        self.reads, self.patches = [], []
        self.read_error = None
        self.patch_error = None

    async def my_offers(self):
        return self.offers

    async def product_offers(self, slug):
        self.reads.append(slug)
        if self.read_error:
            raise self.read_error
        return self.market.get(slug, [])

    async def set_price(self, offer_id, amount):
        self.patches.append((offer_id, amount))
        if self.patch_error:
            raise self.patch_error
        for offer in self.offers:
            if offer["id"] == offer_id:
                offer["amount"] = amount
        return {"id": offer_id, "amount": amount}


def own(oid="o1", slug="harvester", amount=600, **extra):
    return {"id": oid, "amount": amount, "currency": "USD", "quantity": 3, "state": "active",
            "product": {"slug": slug, "name": slug.title()}, **extra}


def rival(amount, oid="r1", seller="other", currency="USD", quantity=1):
    return {"id": oid, "amount": amount, "currency": currency, "quantity": quantity, "seller": {"id": seller}}


LIVE = {"dry_run": False, "rules": {"harvester": 400}}


def run_cycles(client, n=1, clock=None, **settings):
    undercutter = Undercutter(client, Settings(**settings), clock=clock or (lambda: 0.0))

    async def go():
        return [await undercutter.cycle() for _ in range(n)]

    return asyncio.run(go())


def test_lowers_when_live():
    client = FakeClient([own()], {"harvester": [rival(550)]})
    tally = run_cycles(client, **LIVE)[0]
    assert client.patches == [("o1", 549)]
    assert tally["changed"] == 1


def test_dry_run_never_writes(caplog):
    client = FakeClient([own()], {"harvester": [rival(550)]})
    with caplog.at_level(logging.INFO, "undercutter"):
        tally = run_cycles(client, dry_run=True, rules={"harvester": 400})[0]
    assert client.patches == []
    assert tally["lower"] == 1 and tally["changed"] == 0
    assert "DRY RUN" in caplog.text and "$5.49" in caplog.text


def test_no_patch_when_exactly_one_cent_under():
    client = FakeClient([own(amount=549)], {"harvester": [rival(550)]})
    tally = run_cycles(client, **LIVE)[0]
    assert client.patches == [] and tally["on_target"] == 1


def test_second_pass_is_quiet():
    client = FakeClient([own()], {"harvester": [rival(550)]})
    run_cycles(client, n=2, **LIVE)
    assert client.patches == [("o1", 549)]


def test_holds_at_floor_and_says_so(caplog):
    client = FakeClient([own(amount=400)], {"harvester": [rival(300)]})
    with caplog.at_level(logging.INFO, "undercutter"):
        tally = run_cycles(client, **LIVE)[0]
    assert client.patches == []
    assert tally["at_floor"] == 1
    assert "holding at floor $4.00" in caplog.text


def test_stops_at_floor():
    client = FakeClient([own()], {"harvester": [rival(300)]})
    run_cycles(client, **LIVE)
    assert client.patches == [("o1", 400)]


def test_no_floor_means_hands_off():
    client = FakeClient([own()], {"harvester": [rival(1)]})
    tally = run_cycles(client, dry_run=False)[0]
    assert client.patches == [] and client.reads == []
    assert tally["no_floor"] == 1


def test_global_floor_is_used_with_drop_guard():
    client = FakeClient([own()], {"harvester": [rival(500)]})
    run_cycles(client, dry_run=False, global_floor=100)
    assert client.patches == [("o1", 499)]

    client = FakeClient([own()], {"harvester": [rival(150)]})
    tally = run_cycles(client, dry_run=False, global_floor=100)[0]
    assert client.patches == [] and tally["big_drop"] == 1


def test_per_item_floor_skips_drop_guard():
    client = FakeClient([own(amount=1000)], {"harvester": [rival(500)]})
    run_cycles(client, dry_run=False, rules={"harvester": 400}, global_floor=100)
    assert client.patches == [("o1", 499)]


def test_excluded():
    client = FakeClient([own()], {"harvester": [rival(500)]})
    tally = run_cycles(client, dry_run=False, rules={"harvester": None}, global_floor=100)[0]
    assert client.patches == [] and client.reads == [] and tally["excluded"] == 1


def test_own_offers_are_not_rivals():
    me = own()
    client = FakeClient([me], {"harvester": [{**me, "seller": {"id": "other"}}, rival(590, "x", seller="me2")]})
    tally = run_cycles(client, **LIVE)[0]
    assert client.patches == [("o1", 589)]
    assert tally["changed"] == 1


def test_non_usd_rivals_ignored():
    client = FakeClient([own()], {"harvester": [rival(100, currency="EUR"), rival(590, "r2")]})
    run_cycles(client, **LIVE)
    assert client.patches == [("o1", 589)]


def test_failed_read_never_changes_price():
    client = FakeClient([own()], {"harvester": [rival(500)]})
    client.read_error = TemporaryError("HTTP 500")
    tally = run_cycles(client, **LIVE)[0]
    assert client.patches == [] and tally["errors"] == 1


@pytest.mark.parametrize("offer", [
    own(state="unlisted"),
    own(quantity=0),
    own(currency="EUR"),
    own(amount=None),
    own(product="an-id-only"),
    "junk",
])
def test_unusable_own_offers_are_left_alone(offer):
    client = FakeClient([offer], {"harvester": [rival(500)]})
    run_cycles(client, **LIVE)
    assert client.patches == []


def test_cooldown_limits_a_price_war():
    client = FakeClient([own()], {"harvester": [rival(550)]})
    times = iter([0.0, 10.0, 301.0])
    undercutter = Undercutter(client, Settings(**LIVE, cooldown_seconds=300), clock=lambda: next(times))

    async def go():
        await undercutter.cycle()
        client.market["harvester"] = [rival(540)]        # the rival answers
        second = await undercutter.cycle()
        third = await undercutter.cycle()
        return second, third

    second, third = asyncio.run(go())
    assert second["cooldown"] == 1
    assert client.patches == [("o1", 549), ("o1", 539)]
    assert third["changed"] == 1


def test_refused_patch_does_not_stop_the_rest():
    client = FakeClient([own(), own("o2", amount=700)],
                        {"harvester": [rival(550)]})
    client.patch_error = Refused("HTTP 400: Bad amount")
    tally = run_cycles(client, **LIVE)[0]
    assert len(client.patches) == 2 and tally["errors"] == 2


def test_failed_patch_waits_for_cooldown():
    client = FakeClient([own()], {"harvester": [rival(550)]})
    client.patch_error = TemporaryError("timeout")
    run_cycles(client, n=2, **LIVE, cooldown_seconds=300)
    assert len(client.patches) == 1


@pytest.mark.parametrize("error", [AuthError("nope"), RateLimited(30, 429)])
def test_auth_and_rate_limit_end_the_pass(error):
    client = FakeClient([own(), own("o2", slug="luger")],
                        {"harvester": [rival(550)], "luger": [rival(550)]})
    client.patch_error = error
    with pytest.raises(type(error)):
        run_cycles(client, dry_run=False, rules={"harvester": 400, "luger": 400})
    assert len(client.patches) == 1


def test_one_read_per_product():
    client = FakeClient([own("o1"), own("o2", amount=700)], {"harvester": [rival(550)]})
    run_cycles(client, **LIVE)
    assert client.reads == ["harvester"]


def test_follows_the_market_up_and_down_within_limits():
    client = FakeClient([own(amount=700)], {"harvester": [rival(550), rival(620, "r2", seller="s3")]})
    undercutter = Undercutter(client, Settings(**LIVE), clock=lambda: 0.0)

    async def go():
        await undercutter.cycle()                                      # down to 5.49
        client.market["harvester"] = [rival(620, "r2", seller="s3")]   # the 5.50 sold
        await undercutter.cycle()                                      # up to 6.19
        client.market["harvester"] = [rival(900, "r3", seller="s4")]   # only an expensive one left
        await undercutter.cycle()                                      # up to the original 7.00, no further
        await undercutter.cycle()                                      # and it stays there
        client.market["harvester"] = [rival(300, "r3", seller="s4")]   # someone dumps below the floor
        await undercutter.cycle()                                      # down to the floor, no further
        await undercutter.cycle()                                      # and it stays there

    asyncio.run(go())
    assert [amount for _, amount in client.patches] == [549, 619, 700, 400]


def test_raises_are_sent_for_items_with_a_floor_only():
    client = FakeClient([own(amount=500)], {"harvester": [rival(550)]})
    run_cycles(client, dry_run=False)
    assert client.patches == []


def test_repeated_status_is_logged_once(caplog):
    client = FakeClient([own(amount=400)], {"harvester": [rival(300)]})
    with caplog.at_level(logging.INFO, "undercutter"):
        run_cycles(client, n=3, **LIVE)
    assert caplog.text.count("holding at floor") == 1


def test_ceiling_survives_a_restart(tmp_path):
    path = str(tmp_path / "state.json")
    client = FakeClient([own(amount=700)], {"harvester": [rival(550)]})
    run_cycles(client, state_file=path, **LIVE)
    assert client.offers[0]["amount"] == 549

    client.market["harvester"] = [rival(900)]
    run_cycles(client, state_file=path, **LIVE)                  # a new process, same file
    assert client.patches[-1] == ("o1", 700)


def test_price_you_change_becomes_the_new_original():
    client = FakeClient([own(amount=700)], {"harvester": [rival(550)]})
    undercutter = Undercutter(client, Settings(**LIVE), clock=lambda: 0.0)

    async def go():
        await undercutter.cycle()                  # 5.49
        client.offers[0]["amount"] = 650           # you edit it on the site
        client.market["harvester"] = [rival(900)]
        await undercutter.cycle()                  # already at the new original
        client.offers[0]["amount"] = 800           # and again, higher
        await undercutter.cycle()

    asyncio.run(go())
    assert [amount for _, amount in client.patches] == [549]
    assert undercutter.ceilings.records["o1"]["ceiling"] == 800


def test_lost_answer_is_not_mistaken_for_your_change():
    client = FakeClient([own(amount=700)], {"harvester": [rival(550)]})
    undercutter = Undercutter(client, Settings(**LIVE), clock=lambda: 0.0)

    async def go():
        real_set = client.set_price

        async def applied_but_timed_out(offer_id, amount):
            await real_set(offer_id, amount)
            raise TemporaryError("timeout")

        client.set_price = applied_but_timed_out
        await undercutter.cycle()                  # BHOPP applied 5.49, we never heard
        client.set_price = real_set
        await undercutter.cycle()

    asyncio.run(go())
    assert undercutter.ceilings.records["o1"]["ceiling"] == 700


def test_empty_answer_keeps_originals_and_dry_run_saves_nothing(tmp_path):
    path = tmp_path / "state.json"
    client = FakeClient([own(amount=700)], {"harvester": [rival(800)]})
    run_cycles(client, dry_run=True, state_file=str(path), rules={"harvester": 400})
    assert not path.exists()

    run_cycles(client, state_file=str(path), **LIVE)
    client.offers = []
    undercutter = Undercutter(client, Settings(**LIVE, state_file=str(path)))
    asyncio.run(undercutter.cycle())
    assert "o1" in undercutter.ceilings.records


def test_corrupt_state_starts_fresh(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken")
    client = FakeClient([own(amount=700)], {"harvester": [rival(550)]})
    run_cycles(client, state_file=str(path), **LIVE)
    assert client.patches == [("o1", 549)]
    assert '"ceiling": 700' in path.read_text()
