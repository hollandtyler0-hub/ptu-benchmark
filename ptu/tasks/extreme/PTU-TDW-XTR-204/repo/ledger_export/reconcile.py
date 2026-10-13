"""Collapse payment rows to invoice amounts."""
def reconcile(rows):
    totals={}
    for row in rows:
        totals[row['invoice_id']]=int(row['amount'])
    return [{'invoice_id':key,'amount':value} for key,value in totals.items()]

def invoice_totals(rows):
    return {row['invoice_id']: int(row['amount']) for row in reconcile(rows)}

def amount_total(rows):
    return sum(int(row['amount']) for row in rows)

def invoice_count(rows):
    return len(invoice_totals(rows))

def compare_totals(rows, expected):
    actual = amount_total(reconcile(rows))
    return {'actual': actual, 'expected': int(expected), 'difference': actual - int(expected)}

def nonzero_rows(rows):
    return [dict(row) for row in reconcile(rows) if row['amount'] != 0]

def negative_invoices(rows):
    return [row['invoice_id'] for row in reconcile(rows) if row['amount'] < 0]

def merge_reconciliation(left, right):
    return reconcile([*left, *right])

def reconciliation_order(rows):
    return [row['invoice_id'] for row in reconcile(rows)]

def has_duplicate_invoice(rows):
    keys = [row['invoice_id'] for row in rows]
    return len(keys) != len(set(keys))

def reconcile_report(rows):
    result = reconcile(rows)
    return {'input_rows': len(rows), 'invoice_rows': len(result),
            'amount': amount_total(result), 'invoice_ids': [row['invoice_id'] for row in result]}
