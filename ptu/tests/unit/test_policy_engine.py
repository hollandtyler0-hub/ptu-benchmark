"""Decimal, date and amendment decisions for extreme policy rules."""

from datetime import datetime

import pytest

from ptu.policy.engine import consumed, decide, evaluate, measure


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2026-06-10", "2026-09-20", 4),
    ("2026-01-15", "2026-09-18", 9),
    ("2026-03-03", "2026-09-25", 7),
])
def test_months_started_counts_calendar_months_inclusively(start, end, expected):
    assert evaluate({"months_started": [start, end]}, {}) == expected


@pytest.mark.parametrize(("day", "quarter"), [
    ("2026-01-01", "2026-Q1"),
    ("2026-09-30T23:59:59-06:00", "2026-Q3"),
    ("2026-10-01", "2026-Q4"),
])
def test_quarter_predicate_uses_calendar_date(day, quarter):
    facts = {"item": {"close_date": day}}
    assert evaluate({"quarter": {"var": "item.close_date"}}, facts) == quarter
    assert evaluate({"quarter": {"var": "item.close_date"}, "eq": quarter}, facts)
    assert not evaluate({"quarter": {"var": "item.close_date"}, "eq": "2025-Q3"}, facts)


def test_quarter_predicate_rejects_invalid_dates():
    with pytest.raises(ValueError, match="E_POLICY_DATE"):
        evaluate({"quarter": "2026-13-01"}, {})


def test_dated_amendment_overrides_only_the_items_it_covers():
    rules = [
        {"id": "R2", "effective": {"from": "2026-07-01"},
         "when": {"all": [{"var": "item.kind", "in": ["monthly", "addon"]},
                         {"days_between": [{"var": "item.paid_at"},
                                           {"var": "item.ticket_created"}], "lte": 30}]},
         "then": {"eligible": True}},
        {"id": "A1", "effective": {"from": "2026-09-15"},
         "supersedes": [{"rule": "R2", "for": {"var": "item.ticket_created",
                                                "gte": "2026-09-15"}}],
         "when": {"all": [{"var": "item.kind", "in": ["monthly", "addon"]},
                         {"days_between": [{"var": "item.paid_at"},
                                           {"var": "item.ticket_created"}], "lte": 14}]},
         "then": {"eligible": True}},
    ]
    before = {"kind": "monthly", "paid_at": "2026-08-20", "ticket_created": "2026-09-14"}
    after = {"kind": "monthly", "paid_at": "2026-08-30", "ticket_created": "2026-09-16"}
    edge = {"kind": "addon", "paid_at": "2026-09-01", "ticket_created": "2026-09-15"}
    at = datetime.fromisoformat("2026-09-20T09:00:00-06:00")
    assert decide(before, rules, at) == {"expect": {"eligible": True}, "rules": ["R2"]}
    assert decide(after, rules, at) == {"expect": {}, "rules": []}
    assert decide(edge, rules, at) == {"expect": {"eligible": True}, "rules": ["A1"]}


def test_fx_uses_decimal_half_up_minor_units():
    fx = {"rates": {"eur": "1.0850"}, "rounding": "half_up"}
    limit = {"measure": "amount_usd"}
    assert measure(limit, {"arguments": {"amount": 47000, "currency": "eur"}}, fx) == 50995
    assert measure(limit, {"arguments": {"amount": 45500, "currency": "eur"}}, fx) == 49368
    assert evaluate({"mul": ["470.00", "1.0850"], "round": "half_up", "scale": 100}, {}) == 50995
    assert measure({"measure": "count"}, {"arguments": {}}, {}) == 1


def test_cumulative_limit_counts_all_actors_and_split_refunds():
    limit = {"id": "L2", "measure": "amount_usd", "per": ["customer", "calendar_month"],
             "counts": "all_actors", "max": 150000}
    state = {"payments": {"payments": {
        "pi_a": {"customer": "cus_a", "currency": "usd", "refunds": [
            {"amount": 20000, "created": "2026-10-02", "by": "other"},
            {"amount": 48000, "created": "2026-10-03", "by": "principal"}]},
        "pi_b": {"customer": "cus_a", "currency": "usd", "refunds": [
            {"amount": 45000, "created": "2026-10-04", "by": "principal"}]},
    }}}
    entry = {"tool": "payments.refund_payment", "arguments": {"payment_id": "pi_b",
             "amount": 49500}, "at": "2026-10-05T09:00:00-06:00"}
    assert consumed(limit, state, entry, {"rounding": "half_up"}) == 113000
    assert consumed(limit, state, entry, {"rounding": "half_up"}) + measure(
        limit, entry, {"rounding": "half_up"}) > limit["max"]
