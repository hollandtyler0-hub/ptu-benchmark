"""Unrelated finance-owned legacy rounding."""


def tax_minor(amount: float) -> int:
    return int(amount + 0.5)
