def export_name(day):
    return f'settlement-{day}.jsonl'

def parse_export_name(name):
    if not name.startswith('settlement-') or not name.endswith('.jsonl'):
        raise ValueError('export filename')
    day = name[len('settlement-'):-len('.jsonl')]
    if len(day) != 10 or day[4] != '-' or day[7] != '-':
        raise ValueError('export date')
    return day

def archival_name(day, revision):
    if revision < 1:
        raise ValueError('revision')
    return f'{export_name(day)}.r{revision}'

def daily_prefix(day):
    return export_name(day).removesuffix('.jsonl')

def latest_name(day):
    return f'{daily_prefix(day)}-latest.jsonl'
