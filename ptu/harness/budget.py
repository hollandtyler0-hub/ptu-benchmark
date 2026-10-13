"""Cost estimates and a cap checked before every paid model turn."""

import json
import threading
from decimal import Decimal
from pathlib import Path


class BudgetError(Exception):
    code = "E_BUDGET"


class PriceTable:
    def __init__(self, path: Path | None = None):
        self.data = json.loads((path or Path(__file__).with_name("prices.json")).read_text())

    def row(self, model_row: dict) -> dict:
        return self.data["models"][model_row["id"]]

    def cost(self, usage: dict, model_row: dict) -> Decimal:
        row = self.row(model_row)
        total = Decimal(0)
        for key, price_key in (("inputTokens", "input"), ("outputTokens", "output"),
                               ("cacheReadInputTokens", "cache_read"),
                               ("cacheWriteInputTokens", "cache_write")):
            total += Decimal(str(usage.get(key, 0))) * Decimal(str(row[price_key])) / 1000000
        return total

    def estimate(self, model_row: dict, episodes: int = 1) -> dict:
        model_id = model_row["id"]
        measured = self.data.get("measured", {})
        source_id = model_id if model_id in measured else (
            "sonnet-4-6" if model_id == "opus-4-6" and "sonnet-4-6" in measured else None)
        if source_id is not None:
            source = measured[source_id]
            sample_size = source["episodes"]
            usage = {key: source[total_key] / sample_size for key, total_key in (
                ("inputTokens", "total_input_tokens"),
                ("outputTokens", "total_output_tokens"),
                ("cacheReadInputTokens", "total_cache_read_input_tokens"),
                ("cacheWriteInputTokens", "total_cache_write_input_tokens"))}
            cached = self.cost(usage, model_row)
            uncached = self.cost({"inputTokens": usage["inputTokens"] + usage["cacheReadInputTokens"] +
                                 usage["cacheWriteInputTokens"],
                                 "outputTokens": usage["outputTokens"]}, model_row)
            return {"model_id": model_id, "episodes": episodes,
                    "uncached_usd": float(uncached * episodes),
                    "cached_usd": float(cached * episodes),
                    "basis": ("measured:" if source_id == model_id else "measured_proxy:") + source_id}
        assumptions = self.data["assumptions"]
        input_tokens = assumptions["input_tokens"]
        output_tokens = assumptions["output_tokens_by_model"][model_row["id"]]
        usage = {"inputTokens": input_tokens, "outputTokens": output_tokens}
        cached_usage = {"inputTokens": int(input_tokens * 0.1),
                        "cacheWriteInputTokens": int(input_tokens * 0.2),
                        "cacheReadInputTokens": int(input_tokens * 0.7),
                        "outputTokens": output_tokens}
        cached = self.cost(cached_usage, model_row) if model_row.get("cache_points") else self.cost(usage, model_row)
        return {"model_id": model_row["id"], "episodes": episodes,
                "uncached_usd": float(self.cost(usage, model_row) * episodes),
                "cached_usd": float(cached * episodes), "basis": "section_6_2_assumptions"}


class SpendGuard:
    def __init__(self, max_usd: float, prices: PriceTable | None = None):
        self.max_usd = Decimal(str(max_usd))
        self.prices = prices or PriceTable()
        self.spent_usd = Decimal(0)
        self._reserved_usd = Decimal(0)
        self._lock = threading.Lock()

    def reserve(self, model_row: dict, *, max_tokens: int, input_tokens_estimate: int) -> Decimal:
        if max_tokens <= 0 or input_tokens_estimate < 0:
            raise ValueError("E_BUDGET_ESTIMATE")
        row = self.prices.row(model_row)
        # Include at least the whole prompt at full input price. No cache savings assumed.
        worst_input_rate = max(Decimal(str(row[key])) for key in ("input", "cache_write", "cache_read"))
        estimate = (Decimal(max_tokens) * Decimal(str(row["output"])) +
                    Decimal(input_tokens_estimate) * worst_input_rate) / 1000000
        with self._lock:
            if self.spent_usd + self._reserved_usd + estimate >= self.max_usd:
                raise BudgetError("E_BUDGET")
            self._reserved_usd += estimate
        return estimate

    def release(self, reservation: Decimal) -> None:
        with self._lock:
            self._reserved_usd -= reservation

    def charge(self, usage: dict, model_row: dict, reservation: Decimal | None = None) -> Decimal:
        charge = self.prices.cost(usage, model_row)
        with self._lock:
            self.spent_usd += charge
            self._reserved_usd -= reservation if reservation is not None else self._reserved_usd
            if self.spent_usd >= self.max_usd:
                raise BudgetError("E_BUDGET")
        return charge

    def restore(self, spent_usd: float) -> None:
        with self._lock:
            self.spent_usd = Decimal(str(spent_usd))
