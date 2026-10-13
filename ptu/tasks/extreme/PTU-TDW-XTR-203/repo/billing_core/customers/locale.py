def currency_for(customer):
    return customer.get('currency', 'USD')

def locale_for(customer):
    return customer.get('locale', 'en-US')

def receipt_language(customer):
    locale = locale_for(customer)
    return locale.split('-', 1)[0].lower()

def display_currency(customer):
    currency = currency_for(customer)
    return currency.upper()

def account_region(customer):
    locale = locale_for(customer)
    parts = locale.split('-', 1)
    return parts[1].upper() if len(parts) > 1 else 'US'
