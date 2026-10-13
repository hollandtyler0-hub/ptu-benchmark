"""Bedrock Converse loop. Credential construction is lazy and explicit."""

import json
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .budget import BudgetError, SpendGuard


USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens")


class ModelError(Exception):
    def __init__(self, code: str, model_id: str, task_id_sha256: str = "", http_status: int | None = None):
        self.code = code
        self.model_id = model_id
        self.task_id_sha256 = task_id_sha256
        self.http_status = http_status
        super().__init__(code)

    def to_json(self) -> dict:
        return {"code": self.code, "model_id": self.model_id,
                "task_id_sha256": self.task_id_sha256, "http_status": self.http_status}


def live_client(profile: str = "meter-bedrock"):
    # Never constructed by tests or imports. boto3 owns credential resolution.
    import boto3
    return boto3.Session(profile_name=profile).client("bedrock-runtime", region_name="us-east-2")


def _model_error(error: Exception, row: dict, task_hash: str = "") -> ModelError:
    response = getattr(error, "response", {})
    if not isinstance(response, dict):
        response = {}
    code = response.get("Error", {}).get("Code", "")
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    mapped = ("E_ACCESS" if code in {"AccessDeniedException", "UnrecognizedClientException"} else
              "E_THROTTLE" if code in {"ThrottlingException", "TooManyRequestsException"} else "E_MODEL")
    return ModelError(mapped, row["id"], task_hash, status if isinstance(status, int) else None)


def _settings(row: dict) -> dict:
    settings = {}
    if row.get("thinking") == "adaptive":
        settings["additionalModelRequestFields"] = {"thinking": {"type": "adaptive"}}
    return settings


def _cache(row: dict, system: list[dict], tools: list[dict], messages: list[dict]):
    if not row.get("cache_points"):
        return system, tools, messages
    checkpoint = {"cachePoint": {"type": "default"}}
    system = [*system, checkpoint]
    tools = [*tools, checkpoint]
    messages = [{"role": msg["role"], "content": list(msg["content"])} for msg in messages]
    for msg in reversed(messages):
        if msg["role"] == "user":
            msg["content"].append(checkpoint)
            break
    return system, tools, messages


def _usage(response: dict) -> dict:
    usage = response.get("usage", {})
    return {key: int(usage.get(key, 0) or 0) for key in USAGE_KEYS}


def _retry_after(error: Exception) -> float:
    response = getattr(error, "response", {})
    if not isinstance(response, dict):
        return 0.0
    headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    if not isinstance(headers, dict):
        return 0.0
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


async def run_loop(client, model_row: dict, tools, system_text: list[str], user_text: str,
                   guard: SpendGuard, limits: dict, transcript_path: Path, *, tool_config: list[dict] | None = None,
                   task_id_sha256: str = "", max_tokens: int | None = None,
                   progress_path: Path | None = None, previous_spend_usd: float = 0,
                   nudge_finish: bool = False) -> dict:
    """Run one episode; all provider data stays in the untracked run directory."""
    messages = [{"role": "user", "content": [{"text": user_text}]}]
    max_tokens = max_tokens if max_tokens is not None else model_row.get("max_tokens", 2048)
    system = [{"text": part} for part in system_text]
    available = tool_config or []
    totals = {key: 0 for key in USAGE_KEYS}
    episode_spend = 0.0
    turns = calls = 0
    finish_nudges = 0
    start = time.monotonic()
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with transcript_path.open("w", encoding="utf-8") as transcript:
        max_turns = limits.get("max_model_turns", 60)
        for turn_index in range(max_turns):
            if time.monotonic() - start >= limits.get("wall_clock_seconds", 900):
                break
            turn_system, turn_tools, turn_messages = _cache(model_row, system, available, messages)
            request = {"modelId": model_row["profile"], "system": turn_system,
                       "messages": turn_messages, "inferenceConfig": {"maxTokens": max_tokens},
                       **_settings(model_row)}
            if turn_tools:
                request["toolConfig"] = {"tools": turn_tools}
            # A UTF-8 byte is used as a conservative token upper estimate, with no cache discount.
            input_estimate = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
            context_window = int(model_row.get("context_window_tokens", 200000))
            if context_window <= 0:
                raise ValueError("E_CONTEXT_CONFIG")
            if input_estimate + max_tokens + 4096 >= context_window:
                return {"context_exhausted": True, "usage": totals, "model_turns": turns,
                        "tool_calls": calls, "finish_nudges": finish_nudges,
                        "spent_usd": float(guard.spent_usd),
                        "episode_spend_usd": episode_spend}
            reservation = guard.reserve(model_row, max_tokens=max_tokens,
                                        input_tokens_estimate=input_estimate)
            attempt = 0
            waited = 0.0
            while True:
                try:
                    response = client.converse(**request)
                    break
                except Exception as error:
                    mapped = _model_error(error, model_row, task_id_sha256)
                    if mapped.code == "E_THROTTLE" and waited < 300:
                        base = min(60, 2 ** min(attempt + 1, 6))
                        delay = max(base * random.uniform(0.75, 1.25), _retry_after(error))
                        pause = min(delay, 300 - waited)
                        time.sleep(pause)
                        waited += pause
                        attempt += 1
                        continue
                    guard.release(reservation)
                    raise mapped from None
            turns += 1
            usage = _usage(response)
            for key in USAGE_KEYS:
                totals[key] += usage[key]
            transcript.write(json.dumps({"request": request, "response": response}, ensure_ascii=False) + "\n")
            transcript.flush()
            charge = guard.prices.cost(usage, model_row)
            at_cap = False
            try:
                guard.charge(usage, model_row, reservation)
            except BudgetError:
                at_cap = True
            episode_spend += float(charge)
            if progress_path is not None:
                progress = {**totals, "episode_spend_usd": previous_spend_usd + episode_spend,
                            "model_turns": turns}
                temporary = progress_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(progress, sort_keys=True) + "\n")
                temporary.replace(progress_path)
            if at_cap:
                raise BudgetError("E_BUDGET")
            message = response.get("output", {}).get("message", {"role": "assistant", "content": []})
            content = message.get("content", [])
            if not model_row.get("echo_reasoning"):
                content = [block for block in content if "reasoningContent" not in block]
            messages.append({"role": "assistant", "content": content})
            if response.get("stopReason") != "tool_use":
                if nudge_finish and not tools.closed and not finish_nudges and turn_index + 1 < max_turns:
                    messages.append({"role": "user", "content": [{"text":
                        "If you have not called control.finish, call it now with an accurate status and change list."}]})
                    finish_nudges = 1
                    continue
                break
            results = []
            for block in content:
                tool_use = block.get("toolUse")
                if tool_use is None:
                    continue
                if calls >= limits.get("max_tool_calls", 40):
                    result = {"ok": False, "error": "tool_limit"}
                else:
                    result = await tools.call(tool_use["name"], tool_use.get("input", {}))
                    calls += 1
                results.append({"toolResult": {"toolUseId": tool_use["toolUseId"],
                                               "content": [{"json": result}],
                                               "status": "success" if result.get("ok") else "error"}})
            if results:
                messages.append({"role": "user", "content": results})
            if tools.closed or not results or calls >= limits.get("max_tool_calls", 40):
                break
    return {"usage": totals, "model_turns": turns, "tool_calls": calls,
            "finish_nudges": finish_nudges,
            "spent_usd": float(guard.spent_usd), "episode_spend_usd": episode_spend,
            "settings": {**_settings(model_row),
            "cache_points": bool(model_row.get("cache_points")),
            "echo_reasoning": bool(model_row.get("echo_reasoning")), "max_tokens": max_tokens}}


def probe_models(rows: list[dict], client, *, guard: SpendGuard | None = None) -> dict:
    """One tiny call per row; caller supplies a fake or explicitly authorized client."""
    results = {}
    for row in rows:
        if row.get("vendor") == "harness":
            continue
        request = {"modelId": row["profile"], "messages": [{"role": "user", "content": [{"text": "Reply OK."}]}],
                   "inferenceConfig": {"maxTokens": 8}}
        if guard is not None:
            reservation = guard.reserve(row, max_tokens=8, input_tokens_estimate=128)
        else:
            reservation = None
        try:
            response = client.converse(**request)
            if guard is not None:
                guard.charge(_usage(response), row, reservation)
            results[row["id"]] = {"status": "available"}
        except BudgetError:
            raise
        except Exception as error:
            if guard is not None and reservation is not None:
                guard.release(reservation)
            mapped = _model_error(error, row)
            results[row["id"]] = {"status": "blocked", **mapped.to_json()}
    return results
