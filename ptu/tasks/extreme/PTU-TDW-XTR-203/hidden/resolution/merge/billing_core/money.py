"""Minor-unit monetary calculations."""
from fractions import Fraction
EXPONENTS = {"JPY": 0, "USD": 2, "EUR": 2, "GBP": 2, "KWD": 3}
class UnknownCurrency(ValueError):
    pass
def exponent(currency):
    try:
        return EXPONENTS[currency.upper()]
    except KeyError:
        raise UnknownCurrency(currency) from None
def round_minor(value):
    value = Fraction(value)
    sign = -1 if value < 0 else 1
    whole, rest = divmod(abs(value.numerator), value.denominator)
    if 2*rest > value.denominator or (2*rest == value.denominator and whole % 2):
        whole += 1
    return sign * whole

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
