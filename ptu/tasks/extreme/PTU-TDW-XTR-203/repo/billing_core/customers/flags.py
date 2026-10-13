def is_tax_exempt(customer):
    return bool(customer.get('tax_exempt', False))

def requires_manual_review(customer, amount):
    threshold = int(customer.get('manual_review_above', 1000000))
    return abs(int(amount)) >= threshold

def accepts_email_receipts(customer):
    return bool(customer.get('email_receipts', True)) and bool(customer.get('email'))

def has_hold(customer):
    return customer.get('billing_hold') in (True, 'Yes')

def allowed_to_credit(customer):
    return not has_hold(customer) or bool(customer.get('allow_hold_credits'))
