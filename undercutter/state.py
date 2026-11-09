"""Remembers each offer's original price, the ceiling for raises.

Saved to a small JSON file so a restart can't mistake an already-lowered price
for the original. A price this tool did not set is treated as yours, and
becomes the new original.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .pricing import valid_amount

log = logging.getLogger("undercutter")


def _valid(record) -> bool:
    return (isinstance(record, dict) and valid_amount(record.get("ceiling")) and valid_amount(record.get("last"))
            and (record.get("tried") is None or valid_amount(record.get("tried"))))


class Ceilings:
    def __init__(self, path: str = ""):
        self.path = Path(path) if path else None
        self.records: dict[str, dict] = {}
        self.dirty = False
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.records = {k: v for k, v in data.items() if _valid(v)}
            except (OSError, ValueError, AttributeError) as e:
                log.warning("couldn't read %s (%s), original prices start again", self.path, type(e).__name__)

    def observe(self, offer_id: str, current: int) -> int:
        """Record the price BHOPP reports now and return this offer's ceiling."""
        record = self.records.get(offer_id)
        if record is None or current not in (record["last"], record["tried"]):
            self.records[offer_id] = {"ceiling": current, "last": current, "tried": None}
            self.dirty = True
            return current
        if record["last"] != current or record["tried"] is not None:
            record["last"], record["tried"] = current, None
            self.dirty = True
        return record["ceiling"]

    def about_to_set(self, offer_id: str, amount: int) -> None:
        """Noted before sending, so a change whose answer got lost isn't taken for yours."""
        self.records[offer_id]["tried"] = amount
        self.dirty = True
        self.save()

    def was_set(self, offer_id: str, amount: int) -> None:
        self.records[offer_id].update(last=amount, tried=None)
        self.dirty = True

    def forget_except(self, offer_ids: set[str]) -> None:
        for offer_id in set(self.records) - offer_ids:
            del self.records[offer_id]
            self.dirty = True

    def save(self) -> None:
        if not self.dirty or self.path is None:
            return
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(self.records), encoding="utf-8")
            os.replace(temp, self.path)
            self.dirty = False
        except OSError as e:
            log.warning("couldn't save original prices to %s (%s)", self.path, type(e).__name__)
