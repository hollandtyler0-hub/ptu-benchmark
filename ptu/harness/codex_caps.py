"""Fail-closed, per-response limits for the pinned Codex app-server protocol.

The dollar figure is an upper bound at the published GPT-6 Sol long-context
token rates, not a claim about a ChatGPT subscription charge. Native Codex
rollout budgeting is enabled separately as a second stop inside the CLI.
"""

from dataclasses import dataclass, field
from math import floor, isfinite


# https://developers.openai.com/api/docs/models/gpt-6-sol
# Long-context cache writes are at most $5/MTok and output at most $15/MTok.
_INPUT_CEILING_PER_MILLION = 5.0
_OUTPUT_CEILING_PER_MILLION = 15.0


def native_rollout_limit(budget_usd: float) -> str:
    """Return a conservative native token limit, with reminders disabled."""
    if not isfinite(budget_usd) or budget_usd <= 0:
        raise ValueError("E_RUN_CONFIG")
    units = floor(budget_usd * 1_000_000 / _OUTPUT_CEILING_PER_MILLION)
    if units < 1:
        raise ValueError("E_RUN_CONFIG")
    return ("features.rollout_budget={enabled=true,limit_tokens=" + str(units)
            + ",reminder_at_remaining_tokens=[],sampling_token_weight=1.0,"
            + "prefill_token_weight=1.0}")


@dataclass
class CodexCaps:
    max_turns: int
    budget_usd: float
    max_tool_calls: int | None = None
    model_steps: int = 0
    tool_calls: int = 0
    estimated_cost_ceiling_usd: float = 0.0
    _input: int = 0
    _output: int = 0
    _tool_ids: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.max_tool_calls is None:
            self.max_tool_calls = self.max_turns
        if self.max_tool_calls < 1:
            raise ValueError("E_RUN_CONFIG")

    def observe_tool_call(self, item: dict) -> str | None:
        """Count an MCP call at start, before the gateway can return a result."""
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or identity in self._tool_ids:
            raise ValueError("E_CODEX_TOOL_STEP")
        self._tool_ids.add(identity)
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            return "steps"
        return None

    def observe_usage(self, notification: dict) -> str | None:
        """Count one completed response per increasing token usage update."""
        usage = notification.get("tokenUsage")
        if (not isinstance(usage, dict) or not isinstance(usage.get("total"), dict)
                or not isinstance(usage.get("last"), dict)):
            raise ValueError("E_CODEX_USAGE")
        total = usage["total"]
        values = (total.get("inputTokens"), total.get("outputTokens"))
        last = (usage["last"].get("inputTokens"), usage["last"].get("outputTokens"))
        if any(type(value) is not int or value < 0 for value in (*values, *last)):
            raise ValueError("E_CODEX_USAGE")
        input_tokens, output_tokens = values
        if input_tokens < self._input or output_tokens < self._output:
            raise ValueError("E_CODEX_USAGE")
        if (input_tokens, output_tokens) == (self._input, self._output):
            return None
        if last == (0, 0) or last[0] > input_tokens or last[1] > output_tokens:
            raise ValueError("E_CODEX_USAGE")
        self._input, self._output = input_tokens, output_tokens
        self.model_steps += 1
        self.estimated_cost_ceiling_usd = (
            input_tokens * _INPUT_CEILING_PER_MILLION
            + output_tokens * _OUTPUT_CEILING_PER_MILLION) / 1_000_000
        if self.estimated_cost_ceiling_usd >= self.budget_usd:
            return "budget"
        if self.model_steps >= self.max_turns:
            return "max_turns"
        return None

    def complete_turn(self, notification: dict) -> str | None:
        """Require a final measured usage value and recheck spend at completion."""
        if isinstance(notification.get("tokenUsage"), dict):
            stop = self.observe_usage(notification)
            if stop:
                return stop
        if self.model_steps == 0:
            raise ValueError("E_CODEX_USAGE")
        if self.estimated_cost_ceiling_usd >= self.budget_usd:
            return "budget"
        return None

    @property
    def usage(self) -> dict:
        return {"input_tokens": self._input, "output_tokens": self._output}
