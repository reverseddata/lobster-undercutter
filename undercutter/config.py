"""Settings from the environment (and optional .env / floors.txt files)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

MIN_POLL_SECONDS = 30
SKIP_WORDS = {"skip", "exclude", "off"}


class ConfigError(ValueError):
    pass


def dollars_to_cents(text: str) -> int:
    try:
        value = Decimal(text.strip().lstrip("$"))
    except InvalidOperation:
        raise ConfigError(f"{text!r} is not a dollar amount") from None
    if not value.is_finite() or value <= 0:
        raise ConfigError(f"{text!r} must be above zero")
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ConfigError(f"{text!r} has fractions of a cent")
    return int(cents)


def parse_floors(text: str, source: str = "floors") -> dict[str, int | None]:
    """`item = 4.50` or `item = skip`, one per line or comma separated.

    The item is a product slug or full name. Returns {item: cents, or None to exclude}.
    """
    rules: dict[str, int | None] = {}
    for raw in re.split(r"[\n,]", text):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        item, sep, value = line.rpartition("=")
        item, value = item.strip().casefold(), value.strip()
        if not sep or not item or not value:
            raise ConfigError(f"{source}: expected 'item = price' or 'item = skip', got {line!r}")
        try:
            rules[item] = None if value.casefold() in SKIP_WORDS else dollars_to_cents(value)
        except ConfigError as e:
            raise ConfigError(f"{source}: {item}: {e}") from None
    return rules


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int | None = None) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value < low or (high is not None and value > high):
        limit = f"at least {low}" if high is None else f"{low} to {high}"
        raise ConfigError(f"{name} must be {limit}, got {value}")
    return value


@dataclass(frozen=True)
class Limit:
    excluded: bool = False
    floor: int | None = None
    is_global: bool = False


@dataclass(frozen=True)
class Settings:
    token: str = ""
    dry_run: bool = False
    state_file: str = ""
    poll_seconds: int = 60
    step: int = 1
    cooldown_seconds: int = 0
    always_online: bool = False
    global_floor: int | None = None
    global_max_drop_pct: int = 25
    rules: dict[str, int | None] = field(default_factory=dict)

    def limit_for(self, slug: str, name: str) -> Limit:
        keys = [slug.casefold(), name.casefold()]
        found = [self.rules[k] for k in keys if k in self.rules]
        if None in found:
            return Limit(excluded=True)
        if found:
            return Limit(floor=found[0])
        if self.global_floor is not None:
            return Limit(floor=self.global_floor, is_global=True)
        return Limit()


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Settings:
    cwd = cwd or Path.cwd()
    if env is None:
        env = {**load_dotenv(cwd / ".env"), **os.environ}

    rules: dict[str, int | None] = {}
    floors_file = cwd / (env.get("FLOORS_FILE") or "floors.txt")
    if floors_file.is_file():
        rules.update(parse_floors(floors_file.read_text(encoding="utf-8-sig"), floors_file.name))
    rules.update(parse_floors(env.get("FLOORS") or "", "FLOORS"))

    raw_global = (env.get("GLOBAL_FLOOR_USD") or "").strip()
    try:
        global_floor = dollars_to_cents(raw_global) if raw_global else None
    except ConfigError as e:
        raise ConfigError(f"GLOBAL_FLOOR_USD: {e}") from None

    # Railway sets RAILWAY_VOLUME_MOUNT_PATH when a volume is attached.
    volume = (env.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    state_file = (env.get("STATE_FILE") or "").strip() or str(Path(volume) / "state.json" if volume else cwd / "state.json")

    return Settings(
        token=(env.get("BHOPP_TOKEN") or "").strip().strip("'\""),
        dry_run=_bool(env, "DRY_RUN", False),
        state_file=state_file,
        poll_seconds=_int(env, "POLL_SECONDS", 60, MIN_POLL_SECONDS),
        step=_int(env, "UNDERCUT_STEP_CENTS", 1, 1),
        cooldown_seconds=_int(env, "COOLDOWN_SECONDS", 0, 0),
        always_online=_bool(env, "ALWAYS_ONLINE", False),
        global_floor=global_floor,
        global_max_drop_pct=_int(env, "GLOBAL_MAX_DROP_PCT", 25, 0, 99),
        rules=rules,
    )
