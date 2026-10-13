def monthly(plan):
    return plan.get('interval') == 'month'

def annual(plan):
    return plan.get('interval') == 'year'

def interval_count(plan):
    return int(plan.get('interval_count', 1))

def monthly_equivalent(plan):
    price = int(plan['unit_amount'])
    return price if monthly(plan) else price // 12 if annual(plan) else price

def comparable(old, new):
    return old.get('currency') == new.get('currency') and interval_count(old) > 0 and interval_count(new) > 0
