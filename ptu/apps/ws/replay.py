"""Replay opaque shell entries from logged runner-side effects, without a shell."""

from ptu.apps.codehost.app import apply_push


def replay_ws_entry(gateway, row: dict) -> None:
    if not row.get("tool", "").startswith("ws."):
        raise ValueError("E_WS_REPLAY")
    if gateway.state.digest() != row.get("state_sha256_before"):
        raise ValueError("E_WS_REPLAY")
    gateway.clock.tick()
    if gateway.clock.now() != row.get("logical_clock"):
        raise ValueError("E_WS_REPLAY")
    effects = row.get("push_effects", [])
    if not isinstance(effects, list):
        raise ValueError("E_WS_REPLAY")
    resources = [dict(use) for use in row.get("resources", [])]
    for update in effects:
        if not isinstance(update, dict):
            raise ValueError("E_WS_REPLAY")
        repo, ref, new = update.get("repo"), update.get("ref"), update.get("new")
        resource = update.get("resource")
        if (not isinstance(repo, str) or not isinstance(ref, str) or not ref.startswith("refs/heads/")
                or not isinstance(resource, str)
                or resource != f"codehost:branch/{repo}#{ref.removeprefix('refs/heads/')}"):
            raise ValueError("E_WS_REPLAY")
        if {"ref": resource, "access": "write"} not in resources:
            raise ValueError("E_WS_REPLAY")
        if update.get("accepted"):
            repository = gateway.state.docs.get("codehost", {}).get("repos", {}).get(repo)
            if repository is None or not isinstance(new, str):
                raise ValueError("E_WS_REPLAY")
            if update.get("push_protection") == "bypassed":
                actor = gateway.actors.get("codehost")
                if actor is None:
                    raise ValueError("E_WS_REPLAY")
                repository.get("push_protection", {}).get("bypasses", {}).pop(
                    actor.acting_for or actor.principal, None)
            apply_push(gateway.state.docs, repo, ref, new)
            checks = update.get("checks", [])
            if not isinstance(checks, list):
                raise ValueError("E_WS_REPLAY")
            repository.setdefault("checks", []).extend(checks)
    if gateway.state.digest() != row.get("state_sha256_after"):
        raise ValueError("E_WS_REPLAY")
    appended = gateway.log.append(row)
    if appended != row:
        raise ValueError("E_WS_REPLAY")
    if gateway.bound:
        gateway.state.docs["control"]["log_anchor"] = gateway.log.anchor()
    gateway._fire_events(row["tool"], row.get("arguments", {}), row.get("result_status"),
                         row["seq"], effects)
