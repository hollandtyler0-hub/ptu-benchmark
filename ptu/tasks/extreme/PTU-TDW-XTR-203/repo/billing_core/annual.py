"""Annual credit convention adopted after Finance review."""
from datetime import timezone
from fractions import Fraction
from .money import EXPONENTS, UnknownCurrency

def _half_even(value):
    value = Fraction(value)
    sign = -1 if value < 0 else 1
    whole, rest = divmod(abs(value.numerator), value.denominator)
    if 2 * rest > value.denominator or (2 * rest == value.denominator and whole % 2):
        whole += 1
    return sign * whole

def annual_credit(lines, start, end, change_at, currency):
    if currency not in EXPONENTS:
        raise UnknownCurrency(currency)
    if any(v.tzinfo is None for v in (start, end, change_at)):
        raise ValueError("naive datetime")
    left = max(start.astimezone(timezone.utc), min(end.astimezone(timezone.utc), change_at.astimezone(timezone.utc)))
    total = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    remain = end.astimezone(timezone.utc) - left
    share = Fraction((remain.days*86400 + remain.seconds)*1000000 + remain.microseconds,
                     (total.days*86400 + total.seconds)*1000000 + total.microseconds)
    values = [int(line['unit_amount']) * int(line.get('quantity', 1)) for line in lines]
    rounded = [_half_even(v * share) for v in values]
    if rounded:
        rounded[-1] += _half_even(sum(values) * share) - sum(rounded)
    return rounded

def annual_total(lines, start, end, change_at, currency):
    return sum(annual_credit(lines, start, end, change_at, currency))

def annual_invoice_lines(lines, start, end, change_at, currency):
    amounts = annual_credit(lines, start, end, change_at, currency)
    return [{'description': line.get('description', ''), 'amount': amount}
            for line, amount in zip(lines, amounts)]

def annual_credit_summary(lines, start, end, change_at, currency):
    rows = annual_invoice_lines(lines, start, end, change_at, currency)
    return {'currency': currency, 'amount': sum(row['amount'] for row in rows),
            'line_count': len(rows), 'lines': rows}
