"""Produce settlement invoice rows."""
from .ingest import collect
from .disputes import eligible
from .allocate import apply_refunds
from .reconcile import reconcile

def build_export(rows,refunds=(),disputes=()):
    found=collect(rows)
    found=eligible(found,disputes)
    found=apply_refunds(found,refunds)
    grouped=reconcile(found)
    lookup={row['invoice_id']:row for row in grouped}
    return [lookup[key] for key in sorted(lookup)]

def export_total(rows, refunds=(), disputes=()):
    return sum(row['amount'] for row in build_export(rows, refunds, disputes))

def export_invoice_ids(rows, refunds=(), disputes=()):
    return [row['invoice_id'] for row in build_export(rows, refunds, disputes)]

def export_by_currency(batches):
    result = {}
    for currency, payload in batches.items():
        result[currency] = build_export(payload['rows'], payload.get('refunds', ()),
                                        payload.get('disputes', ()))
    return result

def tieout_record(rows, refunds, disputes, expected):
    exported = build_export(rows, refunds, disputes)
    actual = sum(row['amount'] for row in exported)
    return {'expected': int(expected), 'actual': actual, 'difference': actual - int(expected),
            'row_count': len(exported)}

def export_page(rows, offset, limit):
    if offset < 0 or limit <= 0:
        raise ValueError('invalid page')
    return [dict(row) for row in rows[offset:offset+limit]]

def export_with_report(rows, refunds=(), disputes=()):
    exported = build_export(rows, refunds, disputes)
    return {'rows': exported, 'amount': sum(row['amount'] for row in exported),
            'invoices': len(exported)}

def export_matches_target(rows, refunds, disputes, target):
    return export_total(rows, refunds, disputes) == int(target)

def invoice_position(rows, invoice_id):
    for index, row in enumerate(rows):
        if row['invoice_id'] == invoice_id:
            return index
    return None

def export_manifest(day, rows, refunds=(), disputes=()):
    result = export_with_report(rows, refunds, disputes)
    return {'day': day, 'count': result['invoices'],
            'amount': result['amount'],
            'invoice_ids': [row['invoice_id'] for row in result['rows']]}
