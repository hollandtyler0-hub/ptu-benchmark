"""Ingest sandbox ledger rows."""
def collect(rows):
    seen=set();result=[]
    for row in rows:
        key=(row.get('customer'),int(row['amount']))
        if key in seen:continue
        seen.add(key);result.append(dict(row))
    return result

def rows_for_day(rows, day):
    return [dict(row) for row in rows if row.get('created', '')[:10] == day]

def group_by_payment(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row['payment_id'], []).append(dict(row))
    return grouped

def invoice_ids(rows):
    return [row['invoice_id'] for row in rows]

def source_summary(rows):
    return {'rows': len(rows), 'payments': len(set(row['payment_id'] for row in rows)),
            'invoices': len(set(invoice_ids(rows))),
            'minor_units': sum(int(row['amount']) for row in rows)}

def page_rows(rows, page_size):
    if page_size <= 0:
        raise ValueError('page size')
    for offset in range(0, len(rows), page_size):
        yield [dict(row) for row in rows[offset:offset+page_size]]

def payment_rows(rows, payment_id):
    return [dict(row) for row in rows if row['payment_id'] == payment_id]

def append_batch(existing, incoming):
    return [*map(dict, existing), *map(dict, incoming)]

def chronology(rows):
    return sorted((dict(row) for row in rows), key=lambda row: (row['created'], row['payment_id']))

def distinct_customers(rows):
    return sorted({row['customer'] for row in rows})

def customer_rows(rows, customer):
    return [dict(row) for row in rows if row['customer'] == customer]

def batch_digest(rows):
    return tuple((row['payment_id'], row['invoice_id'], int(row['amount'])) for row in rows)

def assert_payment_links(rows):
    for row in rows:
        if not row.get('invoice_id') or not row.get('payment_id'):
            raise ValueError('ledger link missing')
    return True

def order_of_first_invoice(rows):
    return sorted({row['invoice_id'] for row in rows})

def source_audit(rows):
    return {'links_valid': assert_payment_links(rows),
            'first_invoices': order_of_first_invoice(rows),
            'summary': source_summary(rows)}
