"""Stable JSON bytes for state and audit digests."""

import hashlib
import json


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()
