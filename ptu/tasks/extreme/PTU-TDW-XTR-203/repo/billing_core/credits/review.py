"""Human review packet for a reconciled credit proposal."""
def line_total(note):
    return sum(int(line['amount']) for line in note.get('lines', []))

def balance_delta(note):
    return int(note['amount']) - line_total(note)

def compare_issued(proposed, issued):
    return {'amount_delta': int(proposed['amount']) - int(issued['amount']),
            'line_count_delta': len(proposed.get('lines', [])) - len(issued.get('lines', [])),
            'balanced': balance_delta(proposed) == 0}

def evidence_packet(invoice, proposed, issued):
    if invoice.get('id') != issued.get('invoice_id'):
        raise ValueError('invoice mismatch')
    comparison = compare_issued(proposed, issued)
    return {'invoice_id': invoice['id'], 'currency': invoice['currency'],
            'proposed_amount': int(proposed['amount']),
            'issued_amount': int(issued['amount']),
            'comparison': comparison,
            'requires_owner_review': comparison['amount_delta'] != 0 or not comparison['balanced']}

def unexplained_lines(proposed, issued):
    expected = [int(line['amount']) for line in issued.get('lines', [])]
    actual = [int(line['amount']) for line in proposed.get('lines', [])]
    length = max(len(expected), len(actual))
    return [index for index in range(length)
            if (actual[index] if index < len(actual) else None)
            != (expected[index] if index < len(expected) else None)]

def review_summary(packets):
    total_delta = sum(packet['comparison']['amount_delta'] for packet in packets)
    needs_review = [packet['invoice_id'] for packet in packets if packet['requires_owner_review']]
    return {'count': len(packets), 'amount_delta': total_delta,
            'needs_review': needs_review}

def owner_handoff(packet, owner):
    return {'invoice_id': packet['invoice_id'], 'owner': owner,
            'reason': 'issued credit differs' if packet['comparison']['amount_delta'] else 'line reconciliation'}

def reviewable(packets):
    return [packet for packet in packets if packet['requires_owner_review']]

def stable_packet_key(packet):
    return packet['currency'], packet['invoice_id']

def sorted_packets(packets):
    return sorted(packets, key=stable_packet_key)

def totals_by_currency(packets):
    totals = {}
    for packet in packets:
        currency = packet['currency']
        row = totals.setdefault(currency, {'proposed': 0, 'issued': 0})
        row['proposed'] += packet['proposed_amount']
        row['issued'] += packet['issued_amount']
    return totals

def unresolved_count(packets):
    return sum(1 for packet in packets if packet['requires_owner_review'])

def review_complete(packets):
    return bool(packets) and unresolved_count(packets) == 0


def build_handoffs(packets, ownership):
    handoffs = []
    for packet in sorted_packets(reviewable(packets)):
        owner = ownership.get(packet['invoice_id'], 'billing')
        handoffs.append(owner_handoff(packet, owner))
    return handoffs

def merge_review_notes(packets, notes):
    by_invoice = {note['invoice_id']: note for note in notes}
    return [{**packet, 'review_note': by_invoice.get(packet['invoice_id'], {}).get('body', '')}
            for packet in packets]

def reconciliation_matrix(packets):
    rows = []
    for packet in sorted_packets(packets):
        delta = packet['comparison']['amount_delta']
        rows.append({'invoice_id': packet['invoice_id'],
                     'currency': packet['currency'],
                     'proposed': packet['proposed_amount'],
                     'issued': packet['issued_amount'],
                     'delta': delta,
                     'status': 'matched' if delta == 0 and packet['comparison']['balanced'] else 'review'})
    return rows

def packet_ids(packets):
    return [packet['invoice_id'] for packet in sorted_packets(packets)]
