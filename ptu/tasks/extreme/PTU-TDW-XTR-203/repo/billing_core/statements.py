"""Account statement dates."""
from .calendar import period_seconds

def days_in_period(anchor, tz):
    return period_seconds(anchor, tz) // 86400

def statement_window(anchor, tz):
    from .calendar import period_bounds
    start, end = period_bounds(anchor, tz)
    return {'starts_on': start.date().isoformat(), 'ends_before': end.date().isoformat(),
            'calendar_days': days_in_period(anchor, tz)}

def statement_line(invoice_id, amount, currency):
    return {'invoice_id': invoice_id, 'amount': int(amount), 'currency': currency}

def statement_balance(lines):
    return sum(int(line['amount']) for line in lines)

def group_statement_lines(lines):
    groups = {}
    for line in lines:
        groups.setdefault(line['currency'], []).append(dict(line))
    return groups
