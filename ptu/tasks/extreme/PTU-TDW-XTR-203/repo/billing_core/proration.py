"""Plan-change monthly credits."""
from .calendar import period_bounds, period_seconds
from .money import exponent, round_minor

def _values(lines):
    return [int(line['unit_amount']) * int(line.get('quantity', 1)) for line in lines]

def prorate(lines, period_start, change_at, tz, currency):
    exponent(currency)
    start, end = period_bounds(period_start, tz)
    if change_at.tzinfo is None:
        raise ValueError('naive datetime')
    if change_at >= end:
        return [0 for _ in lines]
    if change_at <= start:
        return _values(lines)
    share = (end - change_at).total_seconds() / period_seconds(period_start, tz)
    values = _values(lines)
    rounded = [round_minor(abs(v) * share) * (1 if v >= 0 else -1) for v in values]
    target = round_minor(abs(sum(values)) * share) * (1 if sum(values) >= 0 else -1)
    if rounded:
        rounded[0] += target - sum(rounded)
    return rounded

def credit_for_downgrade(old_lines, new_lines, period_start, change_at, tz, currency):
    old = prorate(old_lines, period_start, change_at, tz, currency)
    new = prorate(new_lines, period_start, change_at, tz, currency)
    return [a - b for a, b in zip(old, new)]

def monthly_total(lines, period_start, change_at, tz, currency):
    return sum(prorate(lines, period_start, change_at, tz, currency))

def monthly_preview(lines, period_start, change_at, tz, currency):
    amounts = prorate(lines, period_start, change_at, tz, currency)
    return [{'line': index, 'amount': amount} for index, amount in enumerate(amounts)]

def changed_line_indices(old_lines, new_lines):
    if len(old_lines) != len(new_lines):
        raise ValueError('line count mismatch')
    return [index for index, (old, new) in enumerate(zip(old_lines, new_lines))
            if _values([old])[0] != _values([new])[0]]
