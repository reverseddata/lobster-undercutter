# lobster-undercutter

Reprices the offers you already have on BHOPP so each one stays exactly 1 cent
under the cheapest other seller, between your floor and the price you listed
it at. It never creates listings. Optionally keeps your account shown as
online. One small process, one core dependency (`aiohttp`, plus
`python-socketio` for always-online).

**Running it changes real prices**, but only on items you gave a floor. Set
`DRY_RUN=true` to only log what it would do.

## Setup

Needs Python 3.11 or newer.

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Put your token in `.env` as `BHOPP_TOKEN` (on bhopp.com: F12, Console,
`localStorage.getItem("bhopp.token")`), set your floors, then:

```bash
python -m undercutter
```

`--once` does a single pass and exits. To see the logic without a token, on
any seller's public offers (never changes anything):

```bash
python -m undercutter preview LobsterShop harvester corrupt
```

## How prices move

| Situation | What happens |
|---|---|
| Someone is cheaper or tied | You drop to 1 cent under them |
| The cheapest other sells out or raises | You raise to 1 cent under the new cheapest |
| That would cross your floor | Your price sits at the floor and the log says so |
| That would go above your original price | Your price sits at the original price |
| Already exactly 1 cent under | No request sent |
| Nobody else selling | Left alone |
| No floor for the item, no global floor | Never touched |
| Excluded with `skip` | Never touched |

Only other sellers' in-stock USD offers count. A price is only sent when it
needs to change. It checks every `POLL_SECONDS` (60 by default).

## Original price (the ceiling)

The original price is what an offer costs the first time the tool sees it. So
start it while your offers are at the prices you want as the maximum. If you
change a price yourself on BHOPP, that becomes the new original.

## Floors

Floors are in dollars. Set them in `FLOORS` or in a `floors.txt` file (see
`floors.example.txt`); `FLOORS` wins when both name an item. Items are matched
by slug (the end of the bhopp.com URL) or full name.

```
harvester = 5.00
corrupt = skip
```

`GLOBAL_FLOOR_USD` covers every item without its own floor. A flat floor is
weak for expensive items, so those items also refuse any single drop bigger
than `GLOBAL_MAX_DROP_PCT` (default 25) and log a warning. Per-item floors skip
that check, since you chose the exact limit.

All settings are explained in `.env.example`.

## Deploy on Railway

1. Create a new project from this GitHub repo. Railway builds the `Dockerfile`.
2. Attach a volume to the service (any mount path, 1 GB is plenty). Without
   one, a redeploy forgets the original prices and the current ones become the
   new ceilings.
3. Under Variables add `BHOPP_TOKEN`, `FLOORS` and any other settings.
   `floors.txt` and `.env` are not deployed, so use `FLOORS`.
4. Deploy.

It needs no port. The token lasts 7 days: when the log warns, paste a new one
into `BHOPP_TOKEN`. An expired token makes it wait and do nothing.

If another tool already keeps this account online, leave `ALWAYS_ONLINE` off.

## Tests

```bash
pip install pytest
```

```bash
python -m pytest
```

BHOPP's API is unofficial and can change at any time.
