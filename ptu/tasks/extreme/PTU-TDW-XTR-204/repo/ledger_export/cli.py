"""JSON line export entry point."""
import json
from .export import build_export

def render(payload):
    rows=build_export(payload['rows'],payload.get('refunds',()),payload.get('disputes',()))
    return '\n'.join(json.dumps(row,separators=(',',':')) for row in rows)

def parse_payload(text):
    payload = json.loads(text)
    if not isinstance(payload, dict) or not isinstance(payload.get('rows'), list):
        raise ValueError('export payload')
    return payload

def render_text(text):
    return render(parse_payload(text))

def parse_json_lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]

def render_summary(payload):
    rows = build_export(payload['rows'], payload.get('refunds', ()),
                        payload.get('disputes', ()))
    return json.dumps({'rows': len(rows), 'amount': sum(int(row['amount']) for row in payload['rows'])},
                      separators=(',', ':'))

def output_document(payload):
    return {'data': parse_json_lines(render(payload)),
            'summary': json.loads(render_summary(payload))}

def render_file(payload, path):
    output = render(payload)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(output)
        if output:
            handle.write('\n')
    return len(parse_json_lines(output))

def load_file(path):
    with open(path, encoding='utf-8') as handle:
        return parse_json_lines(handle.read())

def compare_rendered(left, right):
    return parse_json_lines(left) == parse_json_lines(right)
