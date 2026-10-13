"""Stripe-equivalent payments behaviour exercised through the X1 gateway."""

from ptu.apps.base import Actor
from ptu.common.clock import LogicalClock
from ptu.common.gateway import Gateway
from ptu.common.ids import IdFactory
from ptu.common.log import AuditLog
from ptu.common.policy import Policy, Scope
from ptu.common.registry import Registry
from ptu.common.state import StateStore
from ptu.apps.payments.app import APP
from ptu.apps.payments.fixtures import build, build_with_manifest


def gateway(fixture="payment_10000_usd_succeeded", role="refund_analyst", *, actor=None, clock_cfg=None):
    docs = build(fixture)
    docs["control"] = {"confirmations": [], "escalations": [], "finish": None}
    actor = actor or Actor("agent:finance", "finance", role)
    specs = APP.tool_specs(actor, docs)
    if clock_cfg is not None:
        from ptu.services.control.registry import tool_specs as control_specs
        specs.extend(control_specs(extreme=True))
    registry = Registry(specs)
    policy = Policy(actor.principal, ["payments.*", "control.*"], [Scope("payments:**", "write")])
    return Gateway(registry, StateStore(docs), AuditLog(None), policy, "observe",
                   LogicalClock(), IdFactory(11), actor.principal,
                   apps={"payments": APP}, actors={"payments": actor}, clock_cfg=clock_cfg)


def call(g, name, **arguments):
    return g.call("payments." + name, arguments)


def test_search_and_list_paging_expose_more_than_first_hundred():
    g = gateway()
    template = g.state.docs["payments"]["payments"]["pi_fx1"].copy()
    for index in range(120):
        g.state.docs["payments"]["payments"][f"pi_page_{index:03d}"] = template.copy()
    first = call(g, "search_payments", query="", limit=100)["result"]
    assert len(first["payments"]) == 100 and first["has_more"]
    second = call(g, "search_payments", query="", page=first["next_page"])["result"]
    assert len(second["payments"]) == 21 and not second["has_more"]
    listed = call(g, "list_customer_payments", customer="cus_fx1", limit=100)["result"]
    assert listed["has_more"]
    cursor = listed["payments"][-1]["id"]
    assert len(call(g, "list_customer_payments", customer="cus_fx1", starting_after=cursor)["result"]["payments"]) == 21
    assert call(g, "list_customer_payments", customer="cus_fx1", starting_after="missing")["code"] == "invalid_starting_after"


def test_full_partial_refunds_and_over_refund():
    g = gateway()
    first = call(g, "refund_payment", payment="pi_fx1", amount=6000, reason="requested_by_customer")
    assert first["ok"] and first["result"]["amount"] == 6000
    assert g.state.docs["payments"]["payments"]["pi_fx1"]["amount_refunded"] == 6000
    assert call(g, "refund_payment", payment="pi_fx1", amount=5000,
                reason="requested_by_customer")["code"] == "amount_too_large"
    final = call(g, "refund_payment", payment="pi_fx1", reason="duplicate")
    assert final["result"]["amount"] == 4000
    assert g.state.docs["payments"]["payments"]["pi_fx1"]["refunded"] is True
    assert call(g, "refund_payment", payment="pi_fx1", reason="duplicate")["code"] == "charge_already_refunded"
    assert APP.report_unit(g.log.entries[0]["created_ref"]) == "payments:payment/pi_fx1"


def test_refund_reason_dispute_fraud_and_metadata():
    g = gateway()
    assert call(g, "refund_payment", payment="pi_fx1")["code"] == "reason_required"
    assert call(g, "refund_payment", payment="pi_fx1", reason="other")["code"] == "note_required"
    assert call(g, "refund_payment", payment="pi_fx1", reason="unlisted")["code"] == "invalid_reason"
    disputed = gateway("payment_10000_usd_disputed")
    assert call(disputed, "refund_payment", payment="pi_fx1", reason="duplicate")["code"] == "refund_disputed_payment"
    fraud = call(g, "refund_payment", payment="pi_fx1", amount=1000, reason="fraudulent")
    assert fraud["ok"]
    state = g.state.docs["payments"]
    assert len(state["block_lists"]["cards"]) == len(state["block_lists"]["emails"]) == 1
    refund_id = fraud["result"]["id"]
    assert state["notifications"] == [{"kind": "refund_email", "refund": refund_id,
                                         "customer": "cus_fx1"}]
    assert call(g, "update_refund_metadata", refund=refund_id, metadata={"case": "synthetic"})["ok"]
    assert call(g, "update_refund_metadata", refund=refund_id, metadata={"case": "synthetic"},
                amount=2)["error"] == "invalid_argument"

    no_email = gateway("no_email")
    assert call(no_email, "refund_payment", payment="pi_fx1", reason="duplicate")["ok"]
    assert no_email.state.docs["payments"]["notifications"] == []


def test_bulk_full_only_and_idempotency():
    g = gateway("two_payments")
    once = call(g, "refund_payment", payment="pi_fx1", reason="duplicate", idempotency_key="refund-1")
    again = call(g, "refund_payment", payment="pi_fx1", reason="duplicate", idempotency_key="refund-1")
    assert once["result"] == again["result"]
    assert len(g.state.docs["payments"]["refunds"]) == 1
    assert call(g, "refund_payment", payment="pi_fx2", reason="duplicate",
                idempotency_key="refund-1")["code"] == "idempotency_key_in_use"
    assert call(g, "bulk_refund_payments", payments=["pi_fx2"], amount=100,
                reason="duplicate")["error"] == "invalid_argument"
    assert call(g, "bulk_refund_payments", payments=["pi_fx2"], reason="duplicate")["ok"]
    assert g.state.docs["payments"]["payments"]["pi_fx2"]["refunded"] is True


def test_credit_note_issue_action_open_paid_allocations_and_void():
    g = gateway("open_invoice")
    issued = call(g, "create_credit_note", invoice="in_fx1", amount=2500,
                  reason="order_change")
    assert issued["ok"]
    note = issued["result"]["id"]
    assert g.state.docs["payments"]["invoices"]["in_fx1"]["amount_due"] == 7500
    assert APP.report_unit(g.log.entries[-1]["created_ref"]) == "payments:invoice/in_fx1"
    assert call(g, "void_credit_note", credit_note=note)["ok"]
    assert g.state.docs["payments"]["invoices"]["in_fx1"]["amount_due"] == 10000

    paid = gateway("paid_invoice")
    assert call(paid, "create_credit_note", invoice="in_fx1", amount=2500,
                refund_amount=1000, credit_amount=1000, out_of_band_amount=0,
                reason="duplicate")["code"] == "allocation_mismatch"
    result = call(paid, "create_credit_note", invoice="in_fx1", amount=2500,
                  refund_amount=1000, credit_amount=1000, out_of_band_amount=500,
                  reason="duplicate")
    assert result["ok"]
    assert paid.state.docs["payments"]["customers"]["cus_fx1"]["balance"] == -1000
    assert len(paid.state.docs["payments"]["refunds"]) == 1
    assert call(paid, "void_credit_note", credit_note=result["result"]["id"])["code"] == "credit_note_not_voidable"


def test_credit_note_cap_reason_and_zero_due():
    g = gateway("open_invoice")
    assert call(g, "create_credit_note", invoice="in_fx1", amount=10001,
                reason="duplicate")["code"] == "credit_note_amount_too_large"
    assert call(g, "create_credit_note", invoice="in_fx1", amount=1000,
                reason="other")["code"] == "invalid_reason"
    assert call(g, "create_credit_note", invoice="in_fx1", amount=10000,
                reason="duplicate")["ok"]
    invoice = g.state.docs["payments"]["invoices"]["in_fx1"]
    assert invoice["amount_due"] == 0 and invoice["status"] == "paid"


def test_payment_writes_follow_simclock_across_business_days():
    clock = {"start": "2026-10-05T09:10:00-06:00", "timezone": "America/Denver",
             "business_hours": ["09:00", "17:30"]}
    g = gateway("open_invoice", clock_cfg=clock)
    refund = call(g, "refund_payment", payment="pi_fx1", amount=1000, reason="duplicate")
    assert refund["ok"] and refund["result"]["created"] == clock["start"]
    waited = g.call("control.wait", {"until": "next_business_day"})
    assert waited["ok"] and waited["result"]["now"] == "2026-10-06T09:00:00-06:00"
    note = call(g, "create_credit_note", invoice="in_fx1", amount=1000, reason="duplicate")
    assert note["ok"] and note["result"]["created"] == waited["result"]["now"]


def test_roles_restricted_key_and_search_negation():
    viewer = gateway(role="view_only")
    assert call(viewer, "refund_payment", payment="pi_fx1", reason="duplicate")["code"] == "permission_denied"
    dispute_role = gateway(role="dispute_analyst")
    assert call(dispute_role, "get_dispute", dispute="dp_fx1")["ok"] is False
    assert call(dispute_role, "refund_payment", payment="pi_fx1", reason="duplicate")["code"] == "permission_denied"
    restricted = gateway(actor=Actor("agent:key", "rk_read", "restricted_key"))
    assert call(restricted, "get_payment", payment="pi_fx1")["ok"]
    assert call(restricted, "refund_payment", payment="pi_fx1", reason="duplicate")["code"] == "permission_denied"
    restricted_team = gateway(actor=Actor("agent:key", "rk_team", "restricted_key"))
    restricted_team.state.docs["payments"]["restricted_keys"]["rk_team"] = {"team": "write"}
    assert call(restricted_team, "remove_team_member", user="tm_finance")["code"] == "permission_denied"
    assert call(restricted_team, "update_team_member_role", user="tm_finance", role="analyst")["code"] == "permission_denied"
    result = call(gateway("two_payments"), "search_payments", query="status:succeeded -amount:2000")
    assert [row["id"] for row in result["result"]["payments"]] == ["pi_fx1"]


def test_administrator_can_remove_and_update_team_members_with_last_admin_guard():
    g = gateway(role="administrator")
    team = g.state.docs["payments"]["team"]
    team[:] = [
        {"id": "tm_admin", "name": "Synthetic Admin", "roles": ["administrator"]},
        {"id": "tm_refunds", "name": "Synthetic Refunds", "roles": ["refund_analyst"]},
    ]
    updated = call(g, "update_team_member_role", user="tm_refunds", role="analyst")
    assert updated["ok"] and updated["result"]["roles"] == ["analyst"]
    assert g.log.entries[-1]["resources"] == [
        {"ref": "payments:team_member/tm_refunds", "access": "write"}]
    removed = call(g, "remove_team_member", user="tm_refunds")
    assert removed["ok"] and [row["id"] for row in team] == ["tm_admin"]
    assert g.log.entries[-1]["resources"] == [
        {"ref": "payments:team_member/tm_refunds", "access": "write"}]
    assert call(g, "update_team_member_role", user="tm_admin", role="analyst")["code"] == "last_administrator"
    assert call(g, "remove_team_member", user="tm_admin")["code"] == "last_administrator"

    denied = gateway(role="refund_analyst")
    assert call(denied, "remove_team_member", user="tm_finance")["code"] == "permission_denied"
    specs = {spec.name: spec for spec in APP.tool_specs(Actor("agent:finance", "finance", "administrator"), {})}
    assert specs["payments.remove_team_member"].privilege is True
    assert specs["payments.update_team_member_role"].privilege is True


def test_pii_is_materialized_into_manifest_not_fixture_literals():
    docs, manifest = build_with_manifest("two_payments", world_id="fixture", seed=11)
    customers = docs["payments"]["customers"]
    assert all(customers[cid]["email"] == manifest[f"pii:{cid}/email"] for cid in customers)
    assert all(customers[cid]["card_last4"] == manifest[f"pii:{cid}/payment_method_last4"] for cid in customers)
    assert all(customers[cid]["billing_address"] == manifest[f"pii:{cid}/address"] for cid in customers)
    assert all(len(customers[cid]["card_last4"]) == 4 for cid in customers)


def test_ref_mapping_and_absent_surfaces():
    specs = APP.tool_specs(Actor("agent:test", None, "administrator"), {})
    assert len(specs) == 21
    assert all(spec.surface in APP.surfaces for spec in specs)
    assert "payments.cancel_refund" not in {spec.name for spec in specs}
    assert APP.refs_for_path(["payments", "pi_fx1", "refunds", "0"]) == "payments:payment/pi_fx1"
    assert APP.ancestors("payments:refund/pi_fx1/re_1", {}) == ["payments:payment/pi_fx1"]
    assert APP.bookkeeping(["payments", "idempotency"], "refund-1")


def test_declared_read_only_surfaces_expose_only_reads():
    from ptu.apps.fidelity import load_fidelity

    statuses = {row["id"]: row["status"] for row in load_fidelity("payments")["surfaces"]}
    assert {name for name, status in statuses.items() if status == "certified_read_only"} == {
        "disputes", "settings"}
    specs = APP.tool_specs(Actor("agent:test", None, "administrator"), {})
    assert all(spec.effect == "read" for spec in specs
               if statuses[spec.surface] == "certified_read_only")


def test_financial_resources_resolve_to_payment_or_invoice():
    g = gateway("open_invoice_credited")
    assert call(g, "void_credit_note", credit_note="cn_fx0")["ok"]
    assert g.log.entries[-1]["resources"] == [{"ref": "payments:invoice/in_fx1", "access": "write"}]
    refund = gateway("one_refund")
    assert call(refund, "update_refund_metadata", refund="re_fx0", metadata={"case": "A"})["ok"]
    assert refund.log.entries[-1]["resources"] == [{"ref": "payments:payment/pi_fx1", "access": "write"}]
    search = gateway()
    assert call(search, "search_payments")["ok"]
    assert search.log.entries[-1]["resources"] == [{"ref": "payments:payment/*", "access": "read"}]


def test_metadata_key_brackets_are_rejected():
    g = gateway("one_refund")
    result = call(g, "update_refund_metadata", refund="re_fx0", metadata={"a[b]": "value"})
    assert result["code"] == "metadata_limit"


def test_credit_note_carries_its_invoice_currency():
    # Stripe credit notes always carry a currency (the invoice's). XTR-221 F04.
    g = gateway("open_invoice")
    issued = call(g, "create_credit_note", invoice="in_fx1", amount=2500, reason="order_change")
    assert issued["ok"] and issued["result"]["currency"] == "usd"
    gbp = gateway("open_invoice")
    gbp.state.docs["payments"]["invoices"]["in_fx1"]["currency"] = "gbp"
    note = call(gbp, "create_credit_note", invoice="in_fx1", amount=2500, reason="order_change")
    assert note["ok"] and note["result"]["currency"] == "gbp"
    listed = call(gbp, "list_credit_notes", invoice="in_fx1")["result"]["credit_notes"]
    assert [row["currency"] for row in listed] == ["gbp"]
