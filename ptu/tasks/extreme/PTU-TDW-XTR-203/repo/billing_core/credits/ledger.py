"""A local model of posted invoice and credit entries."""
from collections import defaultdict

def entry(reference, kind, amount, currency, at):
    if kind not in {'invoice', 'payment', 'credit', 'refund'}:
        raise ValueError('entry kind')
    if type(amount) is not int:
        raise TypeError('integer minor units required')
    return {'reference': reference, 'kind': kind, 'amount': amount,
            'currency': currency, 'at': at}

def signed_amount(record):
    if record['kind'] in {'payment', 'credit'}:
        return -int(record['amount'])
    return int(record['amount'])

def account_balance(records):
    return sum(signed_amount(record) for record in records)

def balances_by_currency(records):
    result = defaultdict(int)
    for record in records:
        result[record['currency']] += signed_amount(record)
    return dict(result)

def entries_for_reference(records, reference):
    return [dict(record) for record in records if record['reference'] == reference]

def append_credit(records, reference, note, currency, at):
    amount = int(note['amount'])
    if amount < 0:
        raise ValueError('negative note')
    return [*records, entry(reference, 'credit', amount, currency, at)]

def posted_total(records, kind):
    return sum(int(record['amount']) for record in records if record['kind'] == kind)

def audit_sequence(records):
    previous = None
    for record in records:
        if previous is not None and record['at'] < previous:
            return False
        previous = record['at']
    return True

def reversal(record, at):
    counterpart = {'invoice': 'credit', 'payment': 'refund',
                   'credit': 'invoice', 'refund': 'payment'}[record['kind']]
    return entry(record['reference'], counterpart, int(record['amount']),
                 record['currency'], at)

def balance_at(records, at):
    return account_balance([record for record in records if record['at'] <= at])

def reconcile_reference(records, reference, expected):
    found = entries_for_reference(records, reference)
    return {'reference': reference, 'actual': account_balance(found),
            'expected': int(expected), 'delta': account_balance(found) - int(expected)}

def duplicate_references(records):
    counts = defaultdict(int)
    for record in records:
        counts[(record['reference'], record['kind'])] += 1
    return sorted(key for key, count in counts.items() if count > 1)

def currency_balanced(records):
    return all(amount == 0 for amount in balances_by_currency(records).values())

def match_notes_to_entries(notes, records):
    posted = {(record['reference'], record['amount']) for record in records
              if record['kind'] == 'credit'}
    return {note['invoice_id']: (note['invoice_id'], int(note['amount'])) in posted
            for note in notes}
