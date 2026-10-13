"""Pure tool metadata shared by MCP and in-process execution."""

from dataclasses import dataclass
from typing import Callable, Literal

from .clock import LogicalClock
from .ids import IdFactory
from .policy import tool_allowed
from .tool_docs import TOOL_HELP, schema_with_help


@dataclass(frozen=True)
class ResourceUse:
    ref: str
    access: Literal["read", "write"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    effect: Literal["read", "write", "destructive"]
    privilege: bool
    idempotent: bool
    annotations: dict
    input_schema: dict
    auth_args: tuple[str, ...]
    resources_fn: Callable[[dict, dict], list[ResourceUse]]
    handler: Callable[[dict, dict, LogicalClock, IdFactory], dict]
    description: str = ""
    surface: str = ""
    ui_path: str = ""
    dialog: bool = False
    effects_fn: Callable[[dict, dict, dict], list[ResourceUse]] | None = None

    def __post_init__(self):
        description = self.description or TOOL_HELP.get(
            self.name, "Use " + self.name.replace(".", " ").replace("_", " ") + " in the workspace.")
        if self.ui_path and self.ui_path not in description:
            description = self.ui_path + ". " + description
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "input_schema", schema_with_help(self.name, self.input_schema))


class Registry:
    def __init__(self, specs: list[ToolSpec]):
        self._specs = {}
        for spec in specs:
            if not self._valid_dotted(spec.name) or spec.name in self._specs:
                raise ValueError("E_BAD_TOOL_SPEC")
            self._specs[spec.name] = spec

    @staticmethod
    def _valid_dotted(name: str) -> bool:
        import re
        return isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name) is not None

    def get(self, dotted: str) -> ToolSpec | None:
        return self._specs.get(dotted)

    @staticmethod
    def wire_name(dotted: str) -> str:
        if not Registry._valid_dotted(dotted):
            raise ValueError("E_BAD_TOOL_NAME")
        return dotted.replace(".", "__", 1)

    @staticmethod
    def dotted_name(wire: str) -> str:
        if not isinstance(wire, str) or wire.count("__") != 1:
            raise ValueError("E_BAD_TOOL_NAME")
        dotted = wire.replace("__", ".", 1)
        if not Registry._valid_dotted(dotted):
            raise ValueError("E_BAD_TOOL_NAME")
        return dotted

    def tools_for(self, services: list[str], exposure: str, policy) -> list[ToolSpec]:
        if exposure not in {"full", "policy_only"}:
            raise ValueError("E_BAD_EXPOSURE")
        return [spec for name, spec in self._specs.items()
                if name.split(".", 1)[0] in services and (exposure == "full" or tool_allowed(name, policy))]
