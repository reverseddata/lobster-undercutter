# lobster-undercutter

Have you ever listed an item on BHOPP, gone to get a glass of water, and come
back to find **LobsterShop** selling it for exactly one cent less?

Of course you have. Everybody has. It is the closest thing this marketplace
has to weather.

One whole penny. Not a discount, not a deal, not a competitive strategy
anybody could explain out loud with a straight face. Just one cent, delivered
with the quiet confidence of a man holding a door shut.

This tool is a tribute to that vision. It does the same thing, to everyone,
including him. Especially him.

> "Imitation is the sincerest form of flattery." Consider yourself flattered,
> LobsterShop. You have earned this. Nobody else was willing to be this annoying
> for free.

## Features

- Stays **exactly 1 cent** under the cheapest other seller at all times. A
  career in one line of arithmetic.
- Goes back up when they sell out or raise, because leaving money on the table
  is for people with hobbies.
- Stops at your floor, because unlike a true artist of the craft, you have
  limits.
- Never goes above the price you first listed at. Even pettiness needs a
  budget.
- Optional always-online mode, so you can look as present and engaged as
  someone refreshing the offers page all day.
- One small process and one core dependency (`aiohttp`, plus `python-socketio`
  for always-online). Spite should be lightweight.

It reprices the offers you already have. It does not create listings. You will
have to find your own inventory, as well as your own personality.

**Running it changes real prices**, but only on items you gave a floor. Set
`DRY_RUN=true` if you'd like to merely fantasize about it in the logs.

## Setup

Needs Python 3.11 or newer, and a grudge.

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Put your token in `.env` as `BHOPP_TOKEN` (on bhopp.com: F12, Console,
`localStorage.getItem("bhopp.token")`), set your floors, then unleash the most
powerful force in online commerce, subtraction:

```bash
python -m undercutter
```

`--once` does a single pass and exits, for the emotionally restrained.

To see what it would do without a token, on any seller's public offers. This
never changes anything, and the example below was chosen completely at random:

```bash
python -m undercutter preview LobsterShop harvester corrupt
```

## The business strategy, in full

| Situation | What happens |
|---|---|
| Someone is cheaper or tied | You drop to 1 cent under them. Innovation. |
| The cheapest other sells out or raises | You raise to 1 cent under the new cheapest. Agility. |
| That would cross your floor | You sit at the floor and the log says so. Dignity, briefly. |
| That would go above your original price | You sit at the original price. |
| Already exactly 1 cent under | No request sent. Savor it. |
| Nobody else selling | Left alone. There is no one to bother. |
| No floor for the item, no global floor | Never touched. |
| Excluded with `skip` | Never touched. Mercy exists. |

Only other sellers' in-stock USD offers count. A price is only sent when it
needs to change. It checks every `POLL_SECONDS` (60 by default), which is
roughly how long it takes to get undercut anyway.

## The original price (your ceiling)

The original price is what an offer costs the first time the tool sees it, so
start it while your offers are at the prices you want as the maximum. If you
change a price yourself on BHOPP, that becomes the new original. The tool
respects your decisions, which already puts it ahead of the competition.

## Floors, or: how low will you go

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
that check, since you chose that exact limit and must live with yourself.

All settings are explained in `.env.example`, in a much calmer tone.

## Deploy on Railway

So it can be petty while you sleep:

1. Create a new project from this GitHub repo. Railway builds the `Dockerfile`.
2. Attach a volume to the service (any mount path, 1 GB is plenty). Without
   one, a redeploy forgets the original prices and the current ones become the
   new ceilings. Grudges must be persisted to disk.
3. Under Variables add `BHOPP_TOKEN`, `FLOORS` and any other settings.
   `floors.txt` and `.env` are not deployed, so use `FLOORS`.
4. Deploy.

It needs no port. The token lasts 7 days: when the log warns, paste a new one
into `BHOPP_TOKEN`. An expired token makes it wait and do nothing, like a
listing priced one cent too high.

If another tool already keeps this account online, leave `ALWAYS_ONLINE` off.

## FAQ

**Isn't this exactly what you were complaining about?**
Yes.

**What happens if two people run this against each other?**
You both walk down one cent at a time until someone hits their floor. The
market has never been more efficient or less enjoyable.

**LobsterShop, is that you?**
Your price is too high. It will be corrected within 60 seconds.

## Tests

More care went into these than the premise deserves.

```bash
pip install pytest
```

```bash
python -m pytest
```

BHOPP's API is unofficial and can change at any time. LobsterShop, sadly, cannot.
