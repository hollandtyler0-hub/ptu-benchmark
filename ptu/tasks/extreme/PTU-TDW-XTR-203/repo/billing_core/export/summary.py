def total(rows):
    return sum(int(row['amount']) for row in rows)

def counts_by_currency(rows):
    result = {}
    for row in rows:
        currency = row.get('currency', 'USD')
        result[currency] = result.get(currency, 0) + 1
    return result

def totals_by_currency(rows):
    result = {}
    for row in rows:
        currency = row.get('currency', 'USD')
        result[currency] = result.get(currency, 0) + int(row['amount'])
    return result

def balanced_with_notes(invoices, notes):
    return total(invoices) - total(notes)
