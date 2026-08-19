from __future__ import annotations
from pathlib import Path
from typing import Protocol
from smartctx.inventory import Item

class Harness(Protocol):
    def inventory(self, config_root: Path) -> list[Item]: ...
    def compose(self, kept, all_items, config_root, passthrough): ...
