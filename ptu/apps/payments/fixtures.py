"""Invented payments states; customer PII is materialized only for a render."""

from copy import deepcopy

from ptu.common.secrets import materialize_pii


def build_with_manifest(name: str, *, world_id: str = "payments-fixture", seed: int = 1) -> tuple[dict, dict]:
    customers = {
        "cus_fx1": {"name": "Synthetic Customer One", "email": "", "card_last4": "",
                    "billing_address": "", "balance": 0, "metadata": {}},
        "cus_fx2": {"name": "Synthetic Customer Two", "email": "", "card_last4": "",
                    "billing_address": "", "balance": 0, "metadata": {}},
    }
    pii = materialize_pii(world_id, seed, customers)
    for customer_id, row in customers.items():
        row["email"] = pii[f"pii:{customer_id}/email"]
        row["card_last4"] = pii[f"pii:{customer_id}/payment_method_last4"]
        row["billing_address"] = pii[f"pii:{customer_id}/address"]
    payment = {"customer": "cus_fx1", "amount": 10000, "currency": "usd", "status": "succeeded",
               "description": "Invented purchase", "created": "2026-09-12T12:00:00Z",
               "amount_refunded": 0, "refunded": False, "dispute": None,
               "invoice": None, "refunds": []}
    state = {"payments": {"pi_fx1": payment}, "customers": customers, "invoices": {},
             "refunds": {}, "credit_notes": [], "disputes": {},
             "block_lists": {"cards": [], "emails": []},
             "team": [{"id": "tm_finance", "name": "Synthetic Finance", "roles": ["refund_analyst"]}],
             "settings": {"refund_email": True, "available_balance": 1000000},
             "restricted_keys": {"rk_read": {"payments": "read", "refunds": "none"}},
             "idempotency": {}, "notifications": [], "next_ids": {"refund": 1, "credit_note": 1}}
    if name in {"two_payments", "search_mix"}:
        state["payments"]["pi_fx2"] = {**deepcopy(payment), "customer": "cus_fx2", "amount": 2000,
                                        "description": "Invented second purchase"}
    if name in {"payment_10000_usd_refunded_6000", "payment_10000_usd_refunded", "one_refund",
                "idempotent_refund"}:
        amount = 10000 if name == "payment_10000_usd_refunded" else 6000
        state["payments"]["pi_fx1"].update(amount_refunded=amount, refunded=amount == 10000,
                                              refunds=["re_fx0"])
        state["refunds"]["re_fx0"] = {"id": "re_fx0", "payment": "pi_fx1", "customer": "cus_fx1",
                                       "amount": amount, "currency": "usd", "status": "succeeded",
                                       "reason": "duplicate", "metadata": {},
                                       "created": "2026-09-15T12:00:00Z", "destination": "original"}
        if name == "idempotent_refund":
            state["idempotency"]["fixture-key"] = {
                "request": ("refund_payment", {"payment": "pi_fx1", "reason": "duplicate"}),
                "result": deepcopy(state["refunds"]["re_fx0"]),
            }
    if name in {"payment_10000_usd_disputed", "with_dispute", "closed_dispute", "dispute_inquiry"}:
        state["payments"]["pi_fx1"]["dispute"] = "dp_fx1"
        status = ("won" if name == "closed_dispute" else
                  "warning_needs_response" if name == "dispute_inquiry" else "needs_response")
        state["disputes"]["dp_fx1"] = {"id": "dp_fx1", "payment": "pi_fx1", "status": status,
                                        "amount": 10000, "reason": "general", "created": "2026-09-16T12:00:00Z"}
    if name in {"open_invoice", "paid_invoice", "open_invoice_credited", "paid_invoice_credited"}:
        paid = name.startswith("paid")
        invoice = {"customer": "cus_fx1", "status": "paid" if paid else "open", "lines": [
                   {"id": "il_fx1", "description": "Invented service", "amount": 10000, "quantity": 1}],
                   "total": 10000, "amount_due": 0 if paid else 10000, "number": "SYN-001",
                   "credit_notes": [], "payment": "pi_fx1" if paid else None}
        state["invoices"]["in_fx1"] = invoice
        state["payments"]["pi_fx1"]["invoice"] = "in_fx1"
    if name in {"open_invoice_credited", "paid_invoice_credited"}:
        paid = name.startswith("paid")
        amount = 2500 if paid else 6000
        state["credit_notes"].append({"id": "cn_fx0", "invoice": "in_fx1", "customer": "cus_fx1",
                                      "amount": amount, "reason": "duplicate", "status": "issued",
                                      "pre_payment_amount": 0 if paid else amount,
                                      "post_payment_amount": amount if paid else 0,
                                      "refund_amount": 0, "credit_amount": amount if paid else 0,
                                      "out_of_band_amount": 0, "created": "2026-09-17T12:00:00Z"})
        state["invoices"]["in_fx1"]["credit_notes"] = ["cn_fx0"]
        if paid:
            state["customers"]["cus_fx1"]["balance"] = -amount
        else:
            state["invoices"]["in_fx1"]["amount_due"] -= amount
    if name == "metadata_full":
        state["payments"]["pi_fx1"].update(amount_refunded=1000, refunds=["re_fx0"])
        state["refunds"]["re_fx0"] = {"id": "re_fx0", "payment": "pi_fx1", "customer": "cus_fx1",
                                       "amount": 1000, "currency": "usd", "status": "succeeded",
                                       "reason": "duplicate", "metadata": {f"k{i}": "v" for i in range(50)},
                                       "created": "2026-09-15T12:00:00Z", "destination": "original"}
    if name == "low_balance":
        state["settings"]["available_balance"] = 0
    if name == "no_email":
        state["customers"]["cus_fx1"]["email"] = ""
    if name == "refund_email_off":
        state["settings"]["refund_email"] = False
    if name == "search_emails":
        state["customers"] = {
            "cus_fx1": {"name": "One", "email": "alpha@customers.example.net", "balance": 0},
            "cus_fx2": {"name": "Two", "email": "beta@other.example.net", "balance": 0},
        }
    valid = {"payment_10000_usd_succeeded", "payment_10000_usd_refunded_6000",
             "payment_10000_usd_refunded", "payment_10000_usd_disputed", "two_payments",
             "search_mix", "open_invoice", "paid_invoice", "open_invoice_credited",
             "paid_invoice_credited", "one_refund", "with_dispute", "metadata_full",
             "low_balance", "no_email", "refund_email_off", "closed_dispute", "dispute_inquiry", "search_emails",
             "idempotent_refund"}
    if name not in valid:
        raise ValueError("E_FID_FIXTURE")
    return {"payments": state}, pii


def build(name: str) -> dict:
    return build_with_manifest(name)[0]
