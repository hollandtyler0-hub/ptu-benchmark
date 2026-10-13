def difference(expected, observed):
    return expected - observed

def differences(expected, observed):
    keys = set(expected) | set(observed)
    return {key: difference(expected.get(key, 0), observed.get(key, 0))
            for key in sorted(keys) if expected.get(key, 0) != observed.get(key, 0)}

def total_difference(expected_rows, observed_rows):
    return difference(sum(expected_rows), sum(observed_rows))

def unmatched_invoice_ids(expected, observed):
    return sorted(set(expected) ^ set(observed))

def reconciled(expected, observed):
    return not differences(expected, observed)
