from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping
from ccloadout.jsonstore import read_json, update_json

# Delivery counters live here and never in the entry files: writing them back into
# <repo>/docs/memory/*.md would dirty git-tracked files on every launch (spec §5.5).
@dataclass(frozen=True)
class Usage:
    uses: int = 0
    last_used: str = ""

    def days_since(self, today: date) -> int | None:
        try:
            return (today - date.fromisoformat(self.last_used)).days
        except ValueError:                          # absent or unparsable: no age to report
            return None

def usage_path(config_root: Path) -> Path:
    return config_root / "loadout" / "usage.json"

def _repo_key(repo: Path) -> str:
    return str(repo)

def load_usage(config_root: Path, repo: Path) -> dict[str, Usage]:
    per_repo = read_json(usage_path(config_root)).get(_repo_key(repo))
    if not isinstance(per_repo, dict):
        return {}
    out: dict[str, Usage] = {}
    for entry_id, rec in per_repo.items():
        if isinstance(rec, dict):
            out[entry_id] = Usage(uses=int(rec.get("uses") or 0),
                                  last_used=str(rec.get("last_used") or ""))
    return out

def record_delivery(config_root: Path, repo: Path, entry_ids: Iterable[str],
                    today: date | None = None) -> None:
    ids = [i for i in entry_ids]
    if not ids:                                     # nothing was delivered: nothing to record
        return
    stamp = (today or date.today()).isoformat()
    def bump(data: dict) -> None:
        per_repo = data.setdefault(_repo_key(repo), {})
        if not isinstance(per_repo, dict):
            per_repo = data[_repo_key(repo)] = {}
        for entry_id in ids:
            rec = per_repo.get(entry_id)
            uses = int(rec.get("uses") or 0) if isinstance(rec, dict) else 0
            per_repo[entry_id] = {"uses": uses + 1, "last_used": stamp}
    update_json(usage_path(config_root), bump)

def promoted_ids(usage: Mapping[str, Usage], promote_after: int) -> set[str]:
    return {eid for eid, rec in usage.items() if rec.uses >= promote_after}
