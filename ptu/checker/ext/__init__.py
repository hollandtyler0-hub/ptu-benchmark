"""Extreme-tier replay through the same application gateway used by agents."""

import copy
import hashlib
from dataclasses import replace

from ptu.apps import registered_app
from ptu.apps.base import Actor, AppError
from ptu.checker.legacy_resources import (canonical_resource_row, legacy_chat_member_directory,
                                          legacy_crm_record_error)
from ptu.checker.replay import verify_chain
from ptu.common.canonical import sha256_hex
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway, runtime_policy_dict
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import load_policy
from ptu.common.registry import ResourceUse
from ptu.common.state import StateStore


def _legacy_paged_result(row: dict, response: dict) -> dict | None:
    """Prove a pre-paging signed result was the same first page of records."""
    if row.get("result_status") != "ok" or row.get("arguments", {}).get("after") or row.get(
            "arguments", {}).get("cursor") or row.get("arguments", {}).get("page") or row.get(
            "arguments", {}).get("starting_after"):
        return None
    tool = row.get("tool", "")
    if tool in {"crm.search_deals", "crm.search_companies", "crm.search_contacts",
                "crm.search_tickets", "crm.list_activities"}:
        key = "activities" if tool == "crm.list_activities" else tool.removeprefix("crm.search_")
    elif tool in {"payments.search_payments", "payments.list_customer_payments",
                  "payments.list_refunds", "payments.search_customers", "payments.list_invoices",
                  "payments.list_credit_notes", "payments.list_disputes"}:
        key = {"list_customer_payments": "payments"}.get(tool.split(".", 1)[1],
                tool.split(".", 1)[1].removeprefix("search_").removeprefix("list_"))
    elif tool in {"chat.search_messages", "chat.read_channel_history"}:
        key = "messages"
    else:
        return None
    result = response.get("result") if response.get("ok") else None
    if not isinstance(result, dict) or not isinstance(result.get(key), list):
        return None
    records = result[key]
    if tool.startswith("crm."):
        records = records[:min(row.get("arguments", {}).get("limit", 100), 100)]
    if tool == "chat.read_channel_history":
        records = list(reversed(records))
    return {key: records}


def _call_legacy_crm_record_error(gateway: Gateway, row: dict) -> None:
    """Reproduce only the signed old CRM parser's invalid-record response."""
    tool = row["tool"]
    original_spec = gateway.registry.get(tool)
    original_app = gateway.apps["crm"]
    access = "write" if tool == "crm.create_note" else "read"

    def reject_old_alias(actor, name, arguments, docs):
        raise AppError("invalid_record", "invalid record")

    gateway.registry._specs[tool] = replace(
        original_spec, resources_fn=lambda args, docs: [ResourceUse(args["record"], access)])
    gateway.apps["crm"] = replace(original_app, authorize=reject_old_alias)
    try:
        gateway.call(tool, row["arguments"])
    finally:
        gateway.apps["crm"] = original_app
        gateway.registry._specs[tool] = original_spec


def _call_legacy_chat_directory(gateway: Gateway, row: dict) -> dict:
    """Replay one signed member-directory alias with its corrected user grant."""
    tool = row["tool"]
    original_spec = gateway.registry.get(tool)
    gateway.registry._specs[tool] = replace(
        original_spec, resources_fn=lambda args, docs: [ResourceUse("chat:user/*", "read")])
    try:
        return gateway.call(tool, row["arguments"])
    finally:
        gateway.registry._specs[tool] = original_spec


def verify_extreme_integrity(ctx) -> bool:
    if not verify_chain(ctx.log):
        return False
    from ptu.apps.codehost.app import replay_ci_rows
    with replay_ci_rows(ctx.final_state):
        return (_verify_extreme_integrity(ctx, legacy_wait_events=False) or
                _verify_extreme_integrity(ctx, legacy_wait_events=True))


def _verify_extreme_integrity(ctx, *, legacy_wait_events: bool) -> bool:
    bound = bool(ctx.log and ctx.log[0].get("kind") == "genesis")
    if bound:
        task_hash = hashlib.sha256(ctx.task.id.encode()).hexdigest()
        if (ctx.log[0].get("task_id_sha256") != task_hash or
                ctx.log[0].get("seed_sha256") != sha256_hex(ctx.seed_state) or
                any(row.get("task_id_sha256") != task_hash for row in ctx.log[1:])):
            return False
        anchor = {"entry_count": len(ctx.log), "head_sha256": ctx.log[-1]["entry_sha256"]}
        if ctx.control.get("log_anchor") != anchor:
            return False
    declared = set(ctx.task.apps or {})
    workstation = getattr(ctx.task, "workstation", None) is not None
    expected = (declared - {"ws"}) | ({"ws"} if workstation else set())
    apps = getattr(ctx.registry, "apps", None) or {
        app_id: app for app_id in expected if (app := registered_app(app_id)) is not None
    }
    if set(apps) != expected:
        return False
    actors = getattr(ctx.registry, "actors", None) or {
        app_id: Actor(ctx.task.principal["id"], ctx.task.principal.get("acting_for"),
                      ctx.task.principal["app_roles"].get(app_id, "user")) for app_id in apps
    }
    gateway = Gateway(ctx.registry, StateStore(ctx.seed_state), AuditLog(None),
                      load_policy(runtime_policy_dict(ctx.task.policy)), ctx.task.enforcement,
                      LogicalClock(), IdFactory(ctx.task.seed), ctx.task.policy["principal"],
                      task_id_sha256=hashlib.sha256(ctx.task.id.encode()).hexdigest() if bound else "",
                      events=ctx.task.scripted_events, apps=dict(apps), actors=actors, clock_cfg=ctx.task.clock,
                      replay_events=True, legacy_wait_event_times=legacy_wait_events)
    try:
        for row in ctx.log:
            if row.get("kind") in {"genesis", "event"}:
                continue
            if row.get("tool", "").startswith("ws."):
                opaque = getattr(ctx.registry, "replay_opaque", None)
                if opaque is None:
                    return False
                opaque(gateway, row)
            else:
                legacy_error = legacy_crm_record_error(row)
                legacy_chat = legacy_chat_member_directory(row)
                if legacy_error:
                    _call_legacy_crm_record_error(gateway, row)
                elif legacy_chat:
                    response = _call_legacy_chat_directory(gateway, row)
                else:
                    response = gateway.call(row["tool"], row["arguments"])
                legacy_page = None if legacy_error else _legacy_paged_result(row, response)
                paging_match = (legacy_page is not None and
                                sha256_hex(legacy_page) == row.get("result_sha256"))
                if legacy_error or canonical_resource_row(row) is not row or paging_match:
                    # A signed pre-fix directory row has a different hash from
                    # the corrected replay row. Verify its complete canonical
                    # result, then carry the original signed anchor forward so
                    # every subsequent state digest remains comparable.
                    def comparable(entry, signed=None):
                        value = {key: item for key, item in entry.items()
                                 if key not in {"ts", "entry_sha256", "prev_entry_sha256"}}
                        if signed is not None and "returned_ids" not in signed:
                            value.pop("returned_ids", None)
                        return value
                    expected_row = comparable(row) if legacy_error else canonical_resource_row(comparable(row))
                    if paging_match:
                        expected_row = {**expected_row,
                                        "result_sha256": gateway.log.entries[-1]["result_sha256"]}
                    if expected_row != comparable(gateway.log.entries[-1], row):
                        return False
                    gateway.log.entries[-1] = copy.deepcopy(row)
                    if gateway.bound:
                        gateway.state.docs["control"]["log_anchor"] = gateway.log.anchor()
    except (KeyError, TypeError, ValueError):
        return False
    def comparable(row, signed=None):
        value = {key: item for key, item in row.items()
                 if key not in {"ts", "entry_sha256", "prev_entry_sha256"}}
        if signed is not None and "returned_ids" not in signed:
            value.pop("returned_ids", None)
        return value
    return (len(gateway.log.entries) == len(ctx.log) and
            all(comparable(replayed, original) == comparable(original)
                for replayed, original in zip(gateway.log.entries, ctx.log)) and
            sha256_hex(gateway.state.docs) == sha256_hex(ctx.final_state) and
            ctx.control == ctx.final_state.get("control"))
