"""Load frozen decision points and derive item outcomes without writing state."""

import json
import importlib.util
from pathlib import Path

from ptu.policy.engine import decide


def load_decisions(task) -> list[dict]:
    source = getattr(task, "source_path", None)
    config = getattr(task, "decision_points", None) or {}
    relative = config.get("from")
    if not source or not isinstance(relative, str) or not relative:
        raise ValueError("E_DECISIONS_PATH")
    root = Path(source).resolve().parent
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix != ".json":
        raise ValueError("E_DECISIONS_PATH")
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError("E_DECISIONS_READ") from None
    points = loaded if isinstance(loaded, list) else loaded.get("decision_points") if isinstance(loaded, dict) else None
    if not isinstance(points, list) or any(not isinstance(row, dict) or not row.get("id") for row in points):
        raise ValueError("E_DECISIONS_SHAPE")
    combined = [*points, *config.get("cross_cutting", [])]
    if len({row["id"] for row in combined}) != len(combined):
        raise ValueError("E_DECISIONS_DUPLICATE")
    return combined


def generate(task, seed_state: dict) -> dict:
    """Evaluate item predicates against seed facts; report mismatches, never mutate."""
    points = load_decisions(task)
    oracle_name = (getattr(task, "decision_points", None) or {}).get("oracle")
    if oracle_name:
        root = Path(task.source_path).resolve().parent
        path = (root / oracle_name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("E_DECISIONS_ORACLE")
        try:
            spec = importlib.util.spec_from_file_location("ptu_oracle_" + task.id.replace("-", "_"), path)
            if spec is None or spec.loader is None:
                raise ValueError("E_DECISIONS_ORACLE")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            actual = module.generate(task, seed_state)
        except Exception:
            raise ValueError("E_DECISIONS_ORACLE") from None
        if (not isinstance(actual, dict) or set(actual) != {point["id"] for point in points}
                or any(not isinstance(row, dict) for row in actual.values())):
            raise ValueError("E_DECISIONS_ORACLE")
        rows = [{"dp": point["id"], "expect": actual[point["id"]],
                 "rules": point.get("rules", []),
                 "matches": actual[point["id"]] == point.get("expect", {})}
                for point in points]
        return {"decisions": rows, "mismatched": [row["dp"] for row in rows if not row["matches"]]}
    rules = task.policy.get("rules", [])
    at = (task.clock or {}).get("start", "0001-01-01")
    from datetime import datetime
    at = datetime.fromisoformat(at)
    results = []
    for point in points:
        facts = point.get("facts")
        if facts is None:
            facts = seed_state.get("decision_facts", {}).get(point.get("item"))
        if facts is None:
            results.append({"dp": point["id"], "error": "E_DP_FACTS_MISSING"})
            continue
        governing = [rule for rule in rules if rule.get("id") in point.get("rules", [])]
        outcome = decide(facts, governing, at)
        results.append({"dp": point["id"], **outcome,
                        "matches": outcome["expect"] == point.get("expect", {})})
    return {"decisions": results, "mismatched": [row["dp"] for row in results
                                            if not row.get("matches", False)]}
