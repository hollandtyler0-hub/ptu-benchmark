def timezone_for(customer):
    return customer.get('timezone', 'UTC')

def billing_profile(customer):
    if not customer.get('id'):
        raise ValueError('customer id required')
    return {'id': customer['id'], 'timezone': timezone_for(customer),
            'currency': customer.get('currency', 'USD'),
            'tax_exempt': bool(customer.get('tax_exempt', False))}

def merge_profile(current, changes):
    permitted = {'timezone', 'currency', 'tax_exempt', 'display_name'}
    return {**current, **{key: value for key, value in changes.items() if key in permitted}}

def profile_label(customer):
    return customer.get('display_name') or customer.get('id') or 'Unknown account'
