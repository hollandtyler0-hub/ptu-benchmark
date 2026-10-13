"""Small total policy expression language for extreme decision oracles."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP


_ROUNDING = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}


def _date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _quarter(value) -> str:
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    elif isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            try:
                day = datetime.fromisoformat(value).date()
            except ValueError:
                raise ValueError("E_POLICY_DATE") from None
    else:
        raise ValueError("E_POLICY_DATE")
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def _variable(path: str, facts: dict):
    node = facts
    for component in path.split("."):
        if not isinstance(node, dict) or component not in node:
            raise ValueError("E_POLICY_VAR")
        node = node[component]
    return node


def evaluate(expr, facts: dict):
    """Evaluate a literal or a declared expression without executing source text."""
    if not isinstance(expr, dict):
        return expr
    if "all" in expr:
        return all(bool(evaluate(item, facts)) for item in expr["all"])
    if "any" in expr:
        return any(bool(evaluate(item, facts)) for item in expr["any"])
    if "not" in expr:
        return not bool(evaluate(expr["not"], facts))
    if "days_between" in expr:
        left, right = (evaluate(item, facts) for item in expr["days_between"])
        value = (_date(right) - _date(left)).days
    elif "months_started" in expr:
        left, right = (evaluate(item, facts) for item in expr["months_started"])
        start, end = _date(left), _date(right)
        value = max(0, (end.year - start.year) * 12 + end.month - start.month + 1)
    elif "quarter" in expr:
        value = _quarter(evaluate(expr["quarter"], facts))
    elif "mul" in expr:
        value = Decimal("1")
        for item in expr["mul"]:
            value *= Decimal(str(evaluate(item, facts)))
    elif "add" in expr:
        value = sum((Decimal(str(evaluate(item, facts))) for item in expr["add"]), Decimal(0))
    elif "sub" in expr:
        first, second = (Decimal(str(evaluate(item, facts))) for item in expr["sub"])
        value = first - second
    elif "div" in expr:
        first, second = (Decimal(str(evaluate(item, facts))) for item in expr["div"])
        if not second:
            raise ValueError("E_POLICY_DIV_ZERO")
        value = first / second
    elif "var" in expr:
        value = _variable(expr["var"], facts)
    else:
        raise ValueError("E_POLICY_EXPR")
    for operator in ("eq", "lt", "lte", "gt", "gte", "in"):
        if operator not in expr:
            continue
        operand = evaluate(expr[operator], facts)
        if operator == "eq":
            return value == operand
        if operator == "lt":
            return value < operand
        if operator == "lte":
            return value <= operand
        if operator == "gt":
            return value > operand
        if operator == "gte":
            return value >= operand
        return value in operand
    if "round" in expr:
        mode = _ROUNDING.get(expr["round"])
        if mode is None:
            raise ValueError("E_POLICY_ROUND")
        scale = int(expr.get("scale", 1))
        return int((Decimal(str(value)) * scale).quantize(Decimal("1"), rounding=mode))
    return value


def decide(item: dict, rules: list[dict], at: datetime) -> dict:
    """Resolve active item rules and qualifying amendments in date order."""
    current = _date(at)
    facts = {"item": item}
    active = [rule for rule in rules if rule.get("when") is not None and
              _date(rule.get("effective", {}).get("from", "0001-01-01")) <= current]
    superseded = {row["rule"] for rule in active for row in rule.get("supersedes", [])
                  if evaluate(row.get("for", True), facts)}
    outcome: dict = {}
    used = []
    for rule in sorted(active, key=lambda row: (row.get("effective", {}).get("from", ""), row["id"])):
        if rule["id"] in superseded or not evaluate(rule["when"], facts):
            continue
        outcome.update(rule.get("then", {}))
        used.append(rule["id"])
    return {"expect": outcome, "rules": used}


def measure(limit: dict, entry: dict, fx: dict) -> int:
    """The current action in integer USD cents, with decimal FX rounding."""
    if limit.get("measure") == "count":
        return 1
    args = entry.get("arguments", entry)
    amount = args.get("amount", args.get("credit_amount", args.get("amount_usd", 0)))
    if isinstance(amount, bool) or not isinstance(amount, (int, str, Decimal)):
        raise ValueError("E_LIMIT_AMOUNT")
    currency = str(args.get("currency", "usd")).lower()
    rate = Decimal(str(fx.get("rates", {}).get(currency, "1")))
    mode = _ROUNDING.get(fx.get("rounding", "half_up"))
    if mode is None:
        raise ValueError("E_POLICY_ROUND")
    return int((Decimal(str(amount)) * rate).quantize(Decimal("1"), rounding=mode))


def _rows(container):
    if isinstance(container, dict):
        return container.values()
    return container if isinstance(container, list) else []


def consumed(limit: dict, state_before: dict, entry: dict, fx: dict) -> int:
    """Count prior usage for this authority window, including other actors."""
    arguments = entry.get("arguments", {})
    payments = state_before.get("payments", {})
    payment_map = payments.get("payments", {})
    payment_id = arguments.get("payment_id")
    invoice_id = arguments.get("invoice_id")
    target = payment_map.get(payment_id, {})
    customer = arguments.get("customer", target.get("customer"))
    period = str(entry.get("at", entry.get("timestamp", "")))[:7]
    per = limit.get("per", [])
    dimensions = {per} if isinstance(per, str) else set(per)
    if "payment_or_invoice" in dimensions:
        records = [(payment_id, target)] if payment_id else [
            (key, row) for key, row in payments.get("invoices", {}).items() if key == invoice_id]
    elif "customer" in dimensions:
        records = [(key, row) for key, row in payment_map.items() if row.get("customer") == customer]
    else:
        records = list(payment_map.items())
    total = 0
    for _, record in records:
        for refund in _rows(record.get("refunds", [])):
            if not isinstance(refund, dict) or refund.get("status") == "failed":
                continue
            created = str(refund.get("created", refund.get("created_at", "")))[:7]
            if "calendar_month" in dimensions and created != period:
                continue
            if limit.get("counts") != "all_actors" and refund.get("by") not in (
                    None, state_before.get("principal"), arguments.get("actor")):
                continue
            total += measure(limit, {"arguments": {"amount": refund.get("amount", 0),
                                                   "currency": record.get("currency", "usd")}}, fx)
        for note in _rows(record.get("credit_notes", [])):
            if not isinstance(note, dict) or note.get("status") == "void":
                continue
            created = str(note.get("created", note.get("created_at", "")))[:7]
            if "calendar_month" in dimensions and created != period:
                continue
            total += measure(limit, {"arguments": {"amount": note.get("amount", 0),
                                                   "currency": record.get("currency", "usd")}}, fx)
    return total
