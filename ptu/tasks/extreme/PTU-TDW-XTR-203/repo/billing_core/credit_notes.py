"""Credit note composition for a plan downgrade."""
from .proration import prorate
from .tax.rounding import tax_minor

def build_credit_note(old_lines, new_lines, period_start, change_at, tz, currency, tax_rate=0):
    old = prorate(old_lines, period_start, change_at, tz, currency)
    new = prorate(new_lines, period_start, change_at, tz, currency)
    amounts = [a - b for a, b in zip(old, new)]
    rows = [{'kind': 'plan', 'amount': amount} for amount in amounts]
    if tax_rate:
        rows.append({'kind': 'tax', 'amount': tax_minor(sum(amounts) * tax_rate)})
    net = [{'unit_amount': int(a['unit_amount']) - int(b['unit_amount'])} for a,b in zip(old_lines,new_lines)]
    total = sum(prorate(net,period_start,change_at,tz,currency))
    return {'amount': total + (rows[-1]['amount'] if tax_rate else 0), 'lines': rows}

def note_balanced(note):
    return note['amount'] == sum(int(row['amount']) for row in note['lines'])

def note_line_amounts(note):
    return [int(row['amount']) for row in note['lines']]

def note_for_invoice(invoice_id, old_lines, new_lines, period_start, change_at, tz, currency):
    note = build_credit_note(old_lines, new_lines, period_start, change_at, tz, currency)
    return {'invoice_id': invoice_id, **note}

def summarise_notes(notes):
    totals = {}
    for note in notes:
        invoice = note['invoice_id']
        totals[invoice] = totals.get(invoice, 0) + int(note['amount'])
    return totals
