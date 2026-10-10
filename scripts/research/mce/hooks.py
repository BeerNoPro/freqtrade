"""
Hook = a named, versioned market hypothesis that fires on closed candles.

``condition(df, params)`` returns an int Series in {-1, 0, +1}: the expected direction of the
next ``horizon`` candles (+1 up, -1 down, 0 no opinion). It must be causal.
Changing logic or params means a new ``version``; old statistics stay keyed by the old id.
"""

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


Status = Literal["candidate", "active", "retired"]
Condition = Callable[[pd.DataFrame, dict], pd.Series]
Invalidation = Callable[[pd.DataFrame, dict], pd.Series]

MAX_PARAMS = 3


@dataclass(frozen=True)
class Hook:
    name: str
    version: int
    hypothesis: str
    condition: Condition
    params: dict = field(default_factory=dict)
    horizon: int = 12  # primary horizon in candles, used for gates and cooldown
    timeframes: tuple[str, ...] = ("1h",)
    features: tuple[str, ...] = ()
    invalidation: Invalidation | None = None  # price distance from entry that voids the view
    status: Status = "candidate"
    cooldown: int | None = None  # candles to skip after a firing; defaults to horizon

    def __post_init__(self) -> None:
        if len(self.params) > MAX_PARAMS:
            raise ValueError(f"{self.id}: at most {MAX_PARAMS} params, got {len(self.params)}")
        if self.version < 1:
            raise ValueError(f"{self.name}: version must be >= 1")

    @property
    def id(self) -> str:
        return f"{self.name}@v{self.version}"

    def signal(self, df: pd.DataFrame) -> pd.Series:
        sig = self.condition(df, self.params)
        return sig.fillna(0).clip(-1, 1).astype(int)

    def fingerprint(self) -> str:
        parts = [self.id, json.dumps(self.params, sort_keys=True), str(self.horizon)]
        for fn in (self.condition, self.invalidation):
            if fn is not None:
                parts.append(inspect.getsource(fn))
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, Hook] = {}

    def register(self, hook: Hook) -> Hook:
        if hook.id in self._hooks:
            raise ValueError(f"duplicate hook id {hook.id}")
        self._hooks[hook.id] = hook
        return hook

    def __iter__(self) -> Iterator[Hook]:
        return iter(self._hooks.values())

    def __len__(self) -> int:
        return len(self._hooks)

    def get(self, hook_id: str) -> Hook:
        return self._hooks[hook_id]

    def with_status(self, *status: Status) -> list[Hook]:
        return [h for h in self if h.status in status]

    def fingerprint(self) -> str:
        """Hash of every hook's logic and params; record it before any holdout run."""
        joined = "\n".join(sorted(f"{h.id}:{h.fingerprint()}" for h in self))
        return hashlib.sha256(joined.encode()).hexdigest()[:16]
