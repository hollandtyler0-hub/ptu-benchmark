"""Eligibility checks shared by billing adjustment flows."""
from datetime import datetime

def account_eligible(account):
    if account.get('closed_at'):
        return False
    if account.get('billing_hold') in (True, 'Yes') and not account.get('allow_hold_credits'):
        return False
    return bool(account.get('id'))

def line_eligible(line):
    if line.get('kind') in {'deposit', 'tax', 'manual_adjustment'}:
        return False
    if line.get('nonrefundable'):
        return False
    return int(line.get('quantity', 1)) > 0

def refundable_lines(lines):
    return [dict(line) for line in lines if line_eligible(line)]

def within_window(issued_at, changed_at, days=90):
    if not isinstance(issued_at, datetime) or not isinstance(changed_at, datetime):
        raise TypeError('datetimes required')
    if issued_at.tzinfo is None or changed_at.tzinfo is None:
        raise ValueError('aware datetimes required')
    elapsed = changed_at - issued_at
    return 0 <= elapsed.total_seconds() <= days * 86400

def eligibility_report(account, invoice, change_at):
    reasons = []
    if not account_eligible(account):
        reasons.append('account')
    if invoice.get('status') in {'void', 'draft'}:
        reasons.append('invoice')
    if not refundable_lines(invoice.get('lines', [])):
        reasons.append('lines')
    issued_at = invoice.get('issued_at')
    if isinstance(issued_at, datetime) and not within_window(issued_at, change_at):
        reasons.append('window')
    return {'eligible': not reasons, 'reasons': reasons,
            'invoice_id': invoice.get('id'), 'account_id': account.get('id')}

def required_owner(report):
    if report['eligible']:
        return 'billing'
    if 'invoice' in report['reasons']:
        return 'finance-eng'
    return 'billing-ops'

def refundable_amount(lines):
    return sum(int(line['unit_amount']) * int(line.get('quantity', 1))
               for line in refundable_lines(lines))

def hold_reason(account):
    if account.get('closed_at'):
        return 'closed account'
    if account.get('billing_hold') in (True, 'Yes'):
        return 'billing hold'
    return None

def nonrefundable_codes(lines):
    return [line.get('code', '') for line in lines if not line_eligible(line)]

def gate_credit(account, invoice, change_at):
    report = eligibility_report(account, invoice, change_at)
    if report['eligible']:
        return report
    return {**report, 'owner': required_owner(report),
            'hold_reason': hold_reason(account),
            'nonrefundable_codes': nonrefundable_codes(invoice.get('lines', []))}

def validate_credit_request(account, invoice, change_at):
    report = gate_credit(account, invoice, change_at)
    if not report['eligible']:
        raise ValueError(','.join(report['reasons']))
    return report
