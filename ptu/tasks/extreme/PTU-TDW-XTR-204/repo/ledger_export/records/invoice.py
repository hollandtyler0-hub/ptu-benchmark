def invoice_key(row):
    return row['invoice_id']

def invoice_amount(row):
    return int(row['amount'])

def same_invoice(left, right):
    return invoice_key(left) == invoice_key(right)

def with_amount(row, amount):
    return {**row, 'amount': int(amount)}

def invoice_group(rows):
    result = {}
    for row in rows:
        result.setdefault(invoice_key(row), []).append(dict(row))
    return result

def invoice_order(rows):
    return sorted({invoice_key(row) for row in rows})

def invoice_summary(rows):
    grouped = invoice_group(rows)
    return [{'invoice_id': key, 'rows': len(group),
             'amount': sum(invoice_amount(row) for row in group)}
            for key, group in grouped.items()]

def net_invoice_amount(rows, invoice_id):
    return sum(invoice_amount(row) for row in rows if invoice_key(row) == invoice_id)

def contains_invoice(rows, invoice_id):
    return any(invoice_key(row) == invoice_id for row in rows)

def invoice_positions(rows):
    return {key: index for index, key in enumerate(invoice_order(rows))}

def zero_balance_invoices(rows):
    return [row['invoice_id'] for row in invoice_summary(rows) if row['amount'] == 0]
