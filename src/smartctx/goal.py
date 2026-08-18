from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class GoalResult:
    goal: str
    confidence: float
    source: str

_CACHE = lambda cwd: cwd / ".smartctx" / "goal"

def write_goal_cache(cwd: Path, goal: str) -> None:
    d = cwd / ".smartctx"; d.mkdir(exist_ok=True)
    (d / "goal").write_text(goal.strip() + "\n")

def detect_goal(cwd: Path) -> GoalResult:
    cache = _CACHE(cwd)
    if cache.is_file():
        txt = cache.read_text().strip()
        if txt:
            return GoalResult(goal=txt, confidence=1.0, source="cache")
    tags: list[str] = [cwd.name]
    conf = 0.0
    if (cwd / "pyproject.toml").exists():
        tags.append("python project"); conf += 0.3
    if (cwd / "package.json").exists():
        tags.append("javascript project"); conf += 0.3
    if any(cwd.glob("*.tsx")) or any(cwd.glob("**/*.tsx")):
        tags.append("react frontend"); conf += 0.3
    if any(cwd.glob("README*")):
        conf += 0.1
    if conf == 0.0:
        return GoalResult(goal=cwd.name, confidence=0.0, source="none")
    return GoalResult(goal=" ".join(tags), confidence=min(conf, 1.0), source="signals")
