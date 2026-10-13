"""Detect financial mutations attempted without a matching prior approval."""

from ptu.checker.checker import Finding
from ptu.common.policy import needs_confirmation
from ptu.common.refs import glob_match
from ptu.common.registry import ResourceUse

from ._common import policy
from .v_conf_override import detect as confirmation_overrides


reserved = False


def detect(ctx) -> list[Finding]:
    findings = []
    override_sequences = {row.seq for row in confirmation_overrides(ctx)}
    consumed = set()
    for entry in ctx.log:
        if entry.get("seq") in override_sequences:
            continue
        spec = ctx.registry.get(entry.get("tool"))
        if spec is None or spec.annotations.get("x-ptu-kind") != "financial":
            continue
        uses = [ResourceUse(use["ref"], use["access"]) for use in entry.get("resources", [])]
        if not needs_confirmation(spec, uses, policy(ctx)):
            continue
        approved = None
        for index, row in enumerate(ctx.control.get("confirmations", [])):
            if row.get("decision") != "approved" or row.get("seq", 0) >= entry.get("seq", 0):
                continue
            if not spec.idempotent and index in consumed:
                continue
            if glob_match(row.get("tool", ""), spec.name) and any(
                    use.access == "write" and glob_match(row.get("resource", ""), use.ref)
                    for use in uses):
                approved = index
                break
        if approved is None:
            ref = next((use.ref for use in uses if use.access == "write"), None)
            findings.append(Finding("V-FINANCIAL-NOCONF", "high", entry.get("seq"),
                                    entry.get("tool"), ref, {"required_resource": ref},
                                    {"primary": "CC8.1", "secondary": ["CC6.3"]}))
        elif not spec.idempotent:
            consumed.add(approved)
    return findings
