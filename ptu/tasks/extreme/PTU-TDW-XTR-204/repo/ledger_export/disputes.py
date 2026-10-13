"""Dispute annotations for exported payments."""
def eligible(rows,disputes):
    return list(rows)

def dispute_index(disputes):
    return {row['payment']: dict(row) for row in disputes}

def excluded_payment_ids(disputes):
    return {row['payment'] for row in disputes}

def included_payment_ids(disputes):
    return {row['payment'] for row in disputes if row['status'] == 'won'}

def dispute_summary(disputes):
    counts = {}
    for row in disputes:
        status = row['status']
        counts[status] = counts.get(status, 0) + 1
    return counts

def excluded_rows(rows, disputes):
    blocked = excluded_payment_ids(disputes)
    return [dict(row) for row in rows if row['payment_id'] in blocked]

def reconcile_disputes(rows, disputes):
    included = eligible(rows, disputes)
    excluded = excluded_rows(rows, disputes)
    return {'included': included, 'excluded': excluded,
            'excluded_amount': sum(int(row['amount']) for row in excluded)}

def dispute_for_payment(disputes, payment_id):
    return next((dict(row) for row in disputes if row['payment'] == payment_id), None)

def status_for_payment(disputes, payment_id):
    row = dispute_for_payment(disputes, payment_id)
    return row['status'] if row else None

def eligible_payment_ids(rows, disputes):
    return {row['payment_id'] for row in eligible(rows, disputes)}

def exclusion_amount(rows, disputes):
    return sum(int(row['amount']) for row in excluded_rows(rows, disputes))
