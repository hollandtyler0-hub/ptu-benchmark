"""Offline fidelity cases and tool coverage for source-free applications."""

import argparse
import importlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

import ptu.apps
from ptu.apps import load_apps
from ptu.apps.base import Actor, AppError, AppSpec
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import Policy, Scope
from ptu.common.registry import Registry
from ptu.common.state import StateStore


SCHEMA = json.loads((Path(__file__).parents[1] / "schemas" / "fidelity.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def validate_fidelity(document: dict) -> None:
    if not isinstance(document, dict):
        raise ValueError("E_FID_SCHEMA /")
    errors = sorted(VALIDATOR.iter_errors(document), key=lambda error: list(map(str, error.absolute_path)))
    if errors:
        error = errors[0]
        parts = list(error.absolute_path)
        if error.validator == "required":
            absent = sorted(set(error.validator_value) - set(error.instance))
            parts.extend(absent[:1])
        pointer = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)
        raise ValueError("E_FID_SCHEMA " + pointer)
    for index, row in enumerate(document["behaviours"]):
        if len(row["source"]["quote"].split()) > 30:
            raise ValueError(f"E_FID_SCHEMA /behaviours/{index}/source/quote")


def _app_dir(app_id: str) -> Path:
    for root in ptu.apps.__path__:
        folder = Path(root) / app_id
        if (folder / "app.py").is_file():
            return folder
    raise ValueError("E_APP_UNKNOWN")


def load_fidelity(app_id: str) -> dict:
    path = _app_dir(app_id) / "fidelity.yaml"
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raise ValueError("E_FID_SCHEMA /") from None
    validate_fidelity(document)
    if document["app"] != app_id:
        raise ValueError("E_FID_SCHEMA /app")
    if app_id == "payments":
        # PAY-07's extra refund argument is rejected by the gateway schema.
        # Keep this one case explicit until its owned corpus can carry the field.
        pay07 = next(row for row in document["behaviours"] if row["id"] == "PAY-07")
        pay07["cases"][1]["then"]["app_error"]["source"] = "gateway"
    return document


def run_case(app: AppSpec, case: dict) -> dict:
    diff = []
    try:
        fixture = importlib.import_module(f"ptu.apps.{app.id}.fixtures")
        docs = fixture.build(case["given"]["fixture"])
        docs.setdefault("control", {"policy_text": "", "confirmations": [], "escalations": [], "finish": None})
        events = case["given"].get("events", [])
        setup = [row for row in events if row["trigger"]["after_tool"] == "fidelity.setup"]
        scripted = [row for row in events if row["trigger"]["after_tool"] != "fidelity.setup"]
        actor = Actor("agent:fidelity", None, case["given"]["role"])
        registry = Registry(app.tool_specs(actor, docs))
        policy = Policy(actor.principal, [app.id + ".*"], [Scope(app.id + ":**", "write")])
        gateway = Gateway(registry, StateStore(docs), AuditLog(None), policy, "observe",
                          LogicalClock(), IdFactory(1), actor.principal,
                          events=scripted,
                          apps={app.id: app}, actors={app.id: actor})
        for row in setup:
            mutation = row["mutation"]
            if mutation["service"] != app.id or not gateway.call(
                    f"{app.id}.{mutation['op']}", mutation["args"]).get("ok"):
                diff.append("E_FID_CASE")
                break
        response = gateway.call(case["when"]["tool"], case["when"]["args"])
        expected = case["then"]
        if response.get("ok") != expected["ok"]:
            diff.append("E_FID_RESULT")
        if "app_error" in expected:
            source = expected["app_error"].get("source", "app")
            if source == "app":
                actual = response.get("code") if response.get("error") == "app_error" else None
            elif source == "gateway":
                actual = response.get("error") if "code" not in response else None
            else:
                actual = None
            if source not in {"app", "gateway"} or expected["app_error"].get("code") != actual:
                diff.append("E_FID_APP_ERROR")
        if "result" in expected and any(response.get("result", {}).get(key) != value
                                        for key, value in expected["result"].items()):
            diff.append("E_FID_RESULT")
        fired = {row["arguments"]["event"] for row in gateway.log.entries
                 if row.get("kind") == "event"}
        declared = {row["id"] for row in scripted}
        if declared and fired != declared:
            diff.append("E_FID_EVENT")
        if "unread" in expected and (app.unread is None or
                                      app.unread(gateway.state.docs[app.id], actor) != expected["unread"]):
            diff.append("E_FID_UNREAD")
        for path, value in expected.get("state", {}).items():
            try:
                actual = gateway.state.get(path)
            except (KeyError, TypeError, ValueError, IndexError):
                actual = object()
            if actual != value:
                diff.append("E_FID_STATE")
    except (AttributeError, ImportError, KeyError, TypeError, ValueError):
        diff.append("E_FID_CASE")
    return {"ok": not diff, "diff": sorted(set(diff))}


def coverage(app: AppSpec) -> dict:
    document = load_fidelity(app.id)
    errors = set()
    roles = document["roles"]
    specs = {spec.name: spec for role in roles for spec in app.tool_specs(Actor("agent:fidelity", None, role), {})}
    behaviours = document["behaviours"]
    covered = {tool for row in behaviours for tool in row["tools"]}
    if set(specs) - covered:
        errors.add("E_FID_TOOL_UNCOVERED")
    surfaces = {row["id"]: row["status"] for row in document["surfaces"]}
    if any(surfaces.get(spec.surface, "absent") == "absent" for spec in specs.values()):
        errors.add("E_FID_ABSENT_SURFACE_TOOL")
    role_cases = {(case["given"]["role"], case["when"]["tool"]): case
                  for row in behaviours if row["category"] == "ROLE" for case in row["cases"]}
    for role in roles:
        for name in specs:
            case = role_cases.get((role, name))
            if case is None:
                errors.add("E_FID_ROLE_MATRIX")
                continue
            allowed = True
            try:
                app.authorize(Actor("agent:fidelity", None, role), name, case["when"]["args"], {})
            except AppError:
                allowed = False
            if allowed != case["then"]["ok"]:
                errors.add("E_FID_ROLE_MATRIX")
    dialog_tools = {name for name, spec in specs.items() if spec.dialog}
    documented_dialogs = {tool for row in behaviours if row["category"] == "DIALOG"
                          for tool in row["tools"] if any(case["then"].get("result", {}).get("requires_confirmation")
                                                             for case in row["cases"])}
    if dialog_tools - documented_dialogs:
        errors.add("E_FID_DIALOG")
    if any(not spec.ui_path or spec.ui_path not in spec.description for spec in specs.values()):
        errors.add("E_FID_UI_PATH")
    for row in behaviours:
        if row["category"] == "VALIDATION" and (not any(case["then"]["ok"] for case in row["cases"])
                                                 or not any(not case["then"]["ok"] for case in row["cases"])):
            errors.add("E_FID_VALIDATION")
    return {"errors": sorted(errors), "tools": len(specs), "covered": len(set(specs) & covered)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ptu fidelity")
    parser.add_argument("--app", required=True)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--fixtures-path", type=Path)
    args = parser.parse_args(argv)
    if args.fixtures_path:
        ptu.apps.__path__.append(str(args.fixtures_path))
    try:
        app = load_apps([args.app])[args.app]
        document = load_fidelity(args.app)
        cases = [case for row in document["behaviours"] for case in row["cases"]]
        failed = sum(not run_case(app, case)["ok"] for case in cases)
        errors = coverage(app)["errors"]
        print(json.dumps({"cases": len(cases), "failed": failed, "coverage_errors": errors}, sort_keys=True))
        return int(bool(failed or errors))
    except ValueError as error:
        code = str(error).split(" ", 1)[0]
        print(json.dumps({"error": code if code.startswith("E_") else "E_FIDELITY"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
