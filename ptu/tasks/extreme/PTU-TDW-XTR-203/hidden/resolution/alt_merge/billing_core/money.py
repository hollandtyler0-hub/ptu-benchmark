"""Minor-unit monetary calculations."""
from fractions import Fraction
EXPONENTS = {"JPY": 0, "USD": 2, "EUR": 2, "GBP": 2, "KWD": 3}
class UnknownCurrency(ValueError):
    pass

def exponent(currency):
    normalised = str(currency).upper()
    if normalised not in EXPONENTS:
        raise UnknownCurrency(currency)
    return EXPONENTS[normalised]

def round_minor(value):
    # Python rounds an exact Fraction to the nearest integer with ties to even.
    return round(Fraction(value))

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
