"""Drive one isolated Codex app-server turn and stop it at measured limits.

Only sanitized event identities leave this module. The raw protocol stream stays
in the private run store for later audit; request text and tool arguments are
never copied into the normalized grader stream.
"""

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .codex_caps import CodexCaps


@dataclass
class CodexSessionResult:
    exit_code: int = 1
    timed_out: bool = False
    isolation_error: bool = False
    cap_reason: str | None = None
    caps: CodexCaps | None = None
    events: list[str] = field(default_factory=list)


def _emit(result: CodexSessionResult, event: dict) -> None:
    result.events.append(json.dumps(event, separators=(",", ":")))


def _send(proc, method: str, params: dict | None = None, request_id: int | None = None) -> None:
    message = {"method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
    proc.stdin.flush()


def _normalized_item(item: dict) -> dict:
    kind = item.get("type")
    names = {"mcpToolCall": "mcp_tool_call", "agentMessage": "agent_message",
             "plan": "todo_list"}
    result = {"id": item.get("id"), "type": names.get(kind, kind)}
    if kind == "mcpToolCall":
        result.update({"server": item.get("server"), "tool": item.get("tool"),
                       "status": item.get("status"), "error": item.get("error"),
                       "result": item.get("result") is not None})
    return result


def run_codex_session(proc, transcript_path: Path, *, scratch: Path, system: str,
                      user: str, model: str, effort: str, max_turns: int,
                      budget_usd: float, deadline: float, wall_deadline: float,
                      kill_group) -> CodexSessionResult:
    """Run the pinned v2 stdio protocol with independent wall and usage guards."""
    result = CodexSessionResult(caps=CodexCaps(max_turns, budget_usd))
    phase = "initialize"
    thread_id = None
    turn_id = None
    terminal = False
    closed_at = None
    position = 0
    expired = threading.Event()
    stopped = threading.Event()

    def enforce_deadline():
        while not stopped.is_set():
            remaining = min(deadline - time.monotonic(), wall_deadline - time.time())
            if remaining <= 0:
                expired.set()
                kill_group(proc)
                return
            stopped.wait(min(remaining, 0.2))

    watchdog = threading.Thread(target=enforce_deadline, daemon=True)
    watchdog.start()

    def stop(*, timeout=False, isolation=False, cap=None):
        result.timed_out = timeout
        result.isolation_error = isolation
        result.cap_reason = cap
        kill_group(proc)
        try:
            result.exit_code = proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, TimeoutError):
            result.exit_code = -9
        return result

    try:
        _send(proc, "initialize", {"clientInfo": {"name": "meter_ptu",
                                                "title": "Meter PTU", "version": "0.3"}}, 1)
        with transcript_path.open() as stream:
            while True:
                if expired.is_set() or min(deadline - time.monotonic(), wall_deadline - time.time()) <= 0:
                    return stop(timeout=True)
                stream.seek(position)
                line = stream.readline()
                if not line or not line.endswith("\n"):
                    stream.seek(position)
                    if terminal:
                        if closed_at is None:
                            proc.stdin.close()
                            closed_at = time.monotonic()
                        try:
                            result.exit_code = proc.wait(timeout=0.2)
                            return result
                        except (subprocess.TimeoutExpired, TimeoutError):
                            if time.monotonic() - closed_at > 2:
                                kill_group(proc)
                                result.exit_code = 0
                                return result
                    elif proc.poll() is not None:
                        result.exit_code = proc.returncode
                        result.isolation_error = True
                        return result
                    time.sleep(0.02)
                    continue
                position = stream.tell()
                try:
                    event = json.loads(line)
                except ValueError:
                    return stop(isolation=True)
                if not isinstance(event, dict):
                    return stop(isolation=True)
                if "id" in event and "method" in event:
                    # A server request would introduce an unapproved control path.
                    return stop(isolation=True)
                if event.get("id") == 1 and phase == "initialize":
                    if "error" in event or "result" not in event:
                        return stop(isolation=True)
                    _send(proc, "initialized")
                    _send(proc, "thread/start", {"cwd": str(scratch), "model": model,
                                                 "ephemeral": True, "approvalPolicy": "never",
                                                 "developerInstructions": system}, 2)
                    phase = "thread"
                elif event.get("id") == 2 and phase == "thread":
                    data = event.get("result")
                    thread_id = (data.get("thread") or {}).get("id") if isinstance(data, dict) else None
                    if not isinstance(thread_id, str) or not thread_id:
                        return stop(isolation=True)
                    _emit(result, {"type": "thread.started", "thread_id": thread_id, "model": model})
                    _send(proc, "turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": user}],
                                               "model": model, "effort": effort}, 3)
                    phase = "turn"
                elif event.get("id") == 3 and phase == "turn":
                    data = event.get("result")
                    turn_id = (data.get("turn") or {}).get("id") if isinstance(data, dict) else None
                    if not isinstance(turn_id, str) or not turn_id:
                        return stop(isolation=True)
                    phase = "running"
                elif "id" in event and "method" not in event:
                    return stop(isolation=True)
                elif event.get("method") == "turn/started":
                    _emit(result, {"type": "turn.started"})
                elif event.get("method") == "thread/tokenUsage/updated":
                    params = event.get("params")
                    if (not isinstance(params, dict) or params.get("threadId") != thread_id
                            or turn_id is not None and params.get("turnId") != turn_id):
                        return stop(isolation=True)
                    try:
                        cap = result.caps.observe_usage(params)
                    except ValueError:
                        return stop(isolation=True)
                    if cap:
                        return stop(cap=cap)
                elif event.get("method") in {"item/started", "item/completed"}:
                    params = event.get("params")
                    item = params.get("item") if isinstance(params, dict) else None
                    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                        return stop(isolation=True)
                    if item["type"] == "mcpToolCall" and (event["method"] == "item/started"
                            or item.get("id") not in result.caps._tool_ids):
                        try:
                            cap = result.caps.observe_tool_call(item)
                        except ValueError:
                            return stop(isolation=True)
                        if cap:
                            return stop(cap=cap)
                    if (event["method"] == "item/completed" and item["type"] == "mcpToolCall"
                            and item.get("server") == "ptu" and item.get("status") == "failed"
                            and item.get("error") is not None and item.get("result") is None):
                        return stop(isolation=True)
                    if item["type"] not in {"userMessage", "contextCompaction"}:
                        _emit(result, {"type": event["method"].replace("/", "."),
                                       "item": _normalized_item(item)})
                elif event.get("method") == "turn/completed":
                    params = event.get("params")
                    turn = params.get("turn") if isinstance(params, dict) else None
                    if not isinstance(turn, dict) or (turn_id and turn.get("id") != turn_id):
                        return stop(isolation=True)
                    error = turn.get("error") or {}
                    if "shared rollout token budget exhausted" in str(error):
                        return stop(cap="budget")
                    try:
                        cap = result.caps.complete_turn(params)
                    except ValueError:
                        return stop(isolation=True)
                    if cap:
                        return stop(cap=cap)
                    if turn.get("status") == "completed":
                        _emit(result, {"type": "turn.completed", "usage": result.caps.usage})
                    else:
                        _emit(result, {"type": "turn.failed", "error": {"code": "E_MODEL"}})
                    terminal = True
                # Other app-server notifications are presentation or progress.
    except OSError:
        return stop(timeout=expired.is_set(), isolation=not expired.is_set())
    finally:
        stopped.set()
        watchdog.join(timeout=1)
