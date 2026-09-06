from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from ccloadout.jsonstore import read_json, update_json

# A flag says "a session found this entry wrong or outdated". It is written by whoever noticed —
# including the agent, through `cld memory flag` — and resolved by a human in `cld memory audit`.
# Deliberately a sidecar, not a field in the entry: flagging must not rewrite a git-tracked file,
# and an agent must never hold the pen on the store itself.

@dataclass(frozen=True)
class Flag:
    reason: str
    at: str

def flags_path(config_root: Path) -> Path:
    return config_root / "loadout" / "flags.json"

def load_flags(config_root: Path, repo: Path) -> dict[str, Flag]:
    per_repo = read_json(flags_path(config_root)).get(str(repo))
    if not isinstance(per_repo, dict):
        return {}
    return {eid: Flag(reason=str(rec.get("reason") or ""), at=str(rec.get("at") or ""))
            for eid, rec in per_repo.items() if isinstance(rec, dict)}

def set_flag(config_root: Path, repo: Path, entry_id: str, reason: str,
             today: date | None = None) -> None:
    stamp = (today or date.today()).isoformat()
    def mutate(data: dict) -> None:
        per_repo = data.setdefault(str(repo), {})
        if not isinstance(per_repo, dict):
            per_repo = data[str(repo)] = {}
        per_repo[entry_id] = {"reason": reason, "at": stamp}
    update_json(flags_path(config_root), mutate)

def clear_flag(config_root: Path, repo: Path, entry_id: str) -> None:
    def mutate(data: dict) -> None:
        per_repo = data.get(str(repo))
        if isinstance(per_repo, dict):
            per_repo.pop(entry_id, None)
    update_json(flags_path(config_root), mutate)
