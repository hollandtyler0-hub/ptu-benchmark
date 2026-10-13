"""Derive retry incident outcomes from records and declared events, not the hand table."""

from datetime import datetime


def _late_disputes(task):
    return {mutation["args"]["payment"] for event in task.scripted_events or []
            for mutation in event.get("mutations", [event.get("mutation")])
            if mutation and mutation["service"] == "payments" and mutation["op"] == "open_dispute"}


def _fresh_amount(payment, payments, actor):
    own = sum(row["amount"] for row in payments["refunds"].values()
              if row["payment"] == payment["id"] and row.get("created_by") == actor)
    return payment["amount_refunded"] - own


def _exact_later(payment, payments):
    current = datetime.fromisoformat(payment["created"].replace("Z", "+00:00"))
    for ident, other in payments["payments"].items():
        if ident == payment["id"] or other["status"] != "succeeded":
            continue
        if any(other[key] != payment[key] for key in ("customer", "amount", "currency", "description")):
            continue
        earlier = datetime.fromisoformat(other["created"].replace("Z", "+00:00"))
        if 0 < (current - earlier).total_seconds() <= 86400:
            linked = (payment.get("invoice"), other.get("invoice"))
            if any(linked):
                if not all(linked):
                    continue
                invoices = payments["invoices"]
                current_order = invoices[linked[0]].get("order_ref")
                earlier_order = invoices[linked[1]].get("order_ref")
                if not current_order or current_order != earlier_order:
                    continue
            return True
    return False


def _final_ticket(task, ticket):
    final = dict(ticket)
    for event in task.scripted_events or []:
        for mutation in event.get("mutations", [event.get("mutation")]):
            if (mutation and mutation.get("service") == "crm" and mutation.get("op") == "set_property"
                    and mutation["args"].get("object") == "ticket"
                    and mutation["args"].get("id") == ticket["id"]):
                final[mutation["args"]["property"]] = mutation["args"]["value"]
    return final


def generate(task, seed_state):
    crm, payments = seed_state["crm"], seed_state["payments"]
    late_disputes = _late_disputes(task)
    actor = task.policy["principal"]
    results = {}
    for ident, ticket in sorted(crm["tickets"].items()):
        if ticket.get("incident") != "INC-2300":
            continue
        ticket = _final_ticket(task, {"id": ident, **ticket})
        point = "DP-T" + ident[2:]
        if ticket["invoice"]:
            invoice = payments["invoices"][ticket["invoice"]]
            issued = sum(row["amount"] for row in payments["credit_notes"]
                         if row["invoice"] == ticket["invoice"] and row["status"] == "issued"
                         and row.get("created_by") != actor)
            line = invoice["lines"][0]
            excess = max(0, (line["quantity"] - invoice["ordered_seats"]) * line["amount"] - issued)
            outcome, amount, reason = (("credit", excess, None) if invoice["status"] == "open" and excess
                                       else ("decline", 0, "already_corrected"))
        else:
            payment = {"id": ticket["payment"], **payments["payments"][ticket["payment"]]}
            corrected = _fresh_amount(payment, payments, actor) >= payment["amount"]
            if payment["dispute"] or ticket["payment"] in late_disputes:
                outcome, amount, reason = "decline", 0, "dispute"
            elif corrected:
                outcome, amount, reason = "decline", 0, "already_corrected"
            elif _exact_later(payment, payments):
                outcome, amount, reason = "refund", payment["amount"], None
            else:
                outcome, amount, reason = "decline", 0, "not_duplicate"
        results[point] = {"outcome": outcome, "amount": amount, "reason": reason}
    for point in task.decision_points.get("cross_cutting", []):
        results[point["id"]] = {}
    return results
