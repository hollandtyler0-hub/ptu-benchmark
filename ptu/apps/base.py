"""Product application contract shared by the gateway and fidelity runner."""

from dataclasses import dataclass
from typing import Callable, Sequence

from ptu.common.registry import ToolSpec


@dataclass(frozen=True)
class AppError(Exception):
    code: str
    message: str
    kind: str = "validation"
    field: str | None = None


@dataclass(frozen=True)
class Actor:
    principal: str
    acting_for: str | None
    role: str
    teams: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppSpec:
    id: str
    reference_product: str
    fidelity_version: str
    surfaces: tuple[str, ...]
    report_unit: Callable[[str], str]
    tool_specs: Callable[[Actor, dict], list[ToolSpec]]
    authorize: Callable[[Actor, str, dict, dict], None]
    refs_for_path: Callable[[Sequence[str]], str]
    ancestors: Callable[[str, dict | None], list[str]]
    bookkeeping: Callable[[list[str], str], bool]
    created_ref: Callable[[str, dict, dict | None], str | None]
    external_check: Callable[[str, dict, str], bool] | None
    event_ops: dict[str, Callable]
    unread: Callable[[dict, Actor], dict] | None
