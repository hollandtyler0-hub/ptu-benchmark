def add_on_lines(lines):
    return [line for line in lines if line.get('kind') == 'add_on']

def base_lines(lines):
    return [line for line in lines if line.get('kind') != 'add_on']

def add_on_total(lines):
    return sum(int(line['unit_amount']) * int(line.get('quantity', 1)) for line in add_on_lines(lines))

def with_addon(lines, addon):
    if addon.get('kind') != 'add_on':
        raise ValueError('add-on kind required')
    return [*lines, dict(addon)]

def remove_addon(lines, code):
    return [line for line in lines if not (line.get('kind') == 'add_on' and line.get('code') == code)]
