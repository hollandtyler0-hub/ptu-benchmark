"""Minor-unit monetary calculations."""
EXPONENTS = {"JPY": 0, "USD": 2, "EUR": 2, "GBP": 2, "KWD": 3}
class UnknownCurrency(ValueError):
    pass

def exponent(currency):
    return EXPONENTS.get(currency.upper(), 2)

def round_minor(value):
    return int(float(value) + 0.5)

def minor_scale(currency):
    return 10 ** exponent(currency)

def split_minor(amount, currency):
    scale = minor_scale(currency)
    sign = '-' if amount < 0 else ''
    whole, fractional = divmod(abs(int(amount)), scale)
    return sign, whole, fractional

def sum_minor(amounts, currency):
    exponent(currency)
    result = 0
    for amount in amounts:
        if type(amount) is not int:
            raise TypeError('integer minor units required')
        result += amount
    return result

def allocate_evenly(amount, count):
    if count <= 0:
        raise ValueError('count must be positive')
    quotient, remainder = divmod(int(amount), count)
    result = [quotient] * count
    for index in range(remainder):
        result[index] += 1
    return result
