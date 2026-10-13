def amount(line):
    return int(line['unit_amount']) * int(line.get('quantity', 1))

def total(lines):
    return sum(amount(line) for line in lines)

def positive_lines(lines):
    return [line for line in lines if amount(line) > 0]

def normalise_quantity(line):
    return {**line, 'quantity': int(line.get('quantity', 1))}

def scaled_lines(lines, factor):
    if factor < 0:
        raise ValueError('negative factor')
    return [{**line, 'quantity': int(line.get('quantity', 1)) * factor} for line in lines]
