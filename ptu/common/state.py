"""In-memory service documents with canonical persistence."""

import copy
import json
import re
from pathlib import Path

from .canonical import canonical_json, sha256_hex


class StateStore:
    def __init__(self, docs: dict[str, dict]):
        self.docs = copy.deepcopy(docs)

    @classmethod
    def load(cls, seed_dir: Path) -> "StateStore":
        return cls({p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(seed_dir).glob("*.json"))})

    def get(self, path: str):
        parts = re.findall(r"(?:^|\.)([^.\[\]]+)|\[(['\"])(.*?)\2\]", path)
        if not parts or "".join(x[0] or x[2] for x in parts) == "":
            raise ValueError("E_BAD_STATE_PATH")
        obj = self.docs
        for plain, _, quoted in parts:
            key = plain or quoted
            obj = obj[key]
        return obj

    def snapshot(self) -> dict[str, dict]:
        return copy.deepcopy(self.docs)

    def digest(self) -> str:
        return sha256_hex(self.docs)

    def dump(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for service, doc in self.docs.items():
            (out_dir / f"{service}.json").write_bytes(canonical_json(doc) + b"\n")

    @staticmethod
    def diff(before: dict, after: dict, ref_mapper, ignore_fields: set[str]) -> set[str]:
        changed = set()

        def walk(left, right, path):
            if left == right:
                return
            if isinstance(left, dict) and isinstance(right, dict):
                for key in left.keys() | right.keys():
                    if key not in ignore_fields:
                        walk(left.get(key), right.get(key), path + [key])
            else:
                if path:
                    changed.add(ref_mapper(path[0], path[1:]))

        walk(before, after, [])
        return changed
