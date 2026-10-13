"""Invoice presentation rows."""
from .proration import prorate

def build_lines(lines, period_start, change_at, tz, currency):
    amounts = prorate(lines, period_start, change_at, tz, currency)
    return [{'description': line.get('description', ''), 'amount': int(f'{amount:.0f}')}
            for line, amount in zip(lines, amounts)]

def invoice_total(rows):
    return sum(int(row['amount']) for row in rows)

def invoice_descriptions(rows):
    return [row.get('description', '') for row in rows]

def invoice_payload(account_id, currency, rows):
    if not account_id:
        raise ValueError('account required')
    return {'account': account_id, 'currency': currency,
            'total': invoice_total(rows), 'lines': [dict(row) for row in rows]}

def render_plain(rows, currency):
    return '\n'.join(f"{currency} {row['amount']}: {row.get('description', '')}" for row in rows)
