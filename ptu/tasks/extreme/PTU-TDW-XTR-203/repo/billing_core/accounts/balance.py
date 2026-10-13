def apply_credit(balance, credit):
    return balance - credit

def apply_invoice(balance, amount):
    return balance + int(amount)

def settle(balance, payment):
    return balance - int(payment)

def outstanding(invoices, credits, payments):
    total = sum(int(x) for x in invoices)
    total -= sum(int(x) for x in credits)
    total -= sum(int(x) for x in payments)
    return max(0, total)

def account_position(account, entries):
    return {'account': account, 'balance': sum(int(entry['amount']) for entry in entries),
            'entry_count': len(entries)}
