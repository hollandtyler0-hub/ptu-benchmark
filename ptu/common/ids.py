"""Deterministic per-kind identifiers."""

import hashlib
from collections import defaultdict


class IdFactory:
    def __init__(self, seed: int):
        self.seed = seed
        self._counts = defaultdict(int)

    def next(self, kind: str) -> str:
        if not kind or not kind[0].isalpha():
            raise ValueError("E_BAD_KIND")
        self._counts[kind] += 1
        count = self._counts[kind]
        suffix = hashlib.sha256(f"{self.seed}:{kind}:{count}".encode()).hexdigest()[:8]
        return f"{kind[0].lower()}_{count:04x}{suffix}"
