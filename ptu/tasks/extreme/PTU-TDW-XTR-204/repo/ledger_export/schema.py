"""Export schema validation."""
COLUMNS=('invoice_id','amount')
def validate(row):
    return set(row)==set(COLUMNS) and isinstance(row['invoice_id'],str) and type(row['amount']) is int

def require_valid(row):
    if not validate(row):
        raise ValueError('invalid export row')
    return dict(row)

def validate_all(rows):
    return all(validate(row) for row in rows)

def canonical_row(invoice_id, amount):
    row = {'invoice_id': str(invoice_id), 'amount': int(amount)}
    return require_valid(row)

def project_rows(rows):
    return [canonical_row(row['invoice_id'], row['amount']) for row in rows]

def column_names():
    return list(COLUMNS)

def schema_summary(rows):
    invalid = [index for index, row in enumerate(rows) if not validate(row)]
    return {'rows': len(rows), 'invalid_indices': invalid, 'valid': not invalid}

def diff_columns(row):
    return {'missing': sorted(set(COLUMNS)-set(row)), 'extra': sorted(set(row)-set(COLUMNS))}

def with_columns(row):
    return {key: row[key] for key in COLUMNS}

def column_types(row):
    return {key: type(row[key]).__name__ for key in COLUMNS if key in row}

def export_shape(rows):
    return {'columns': column_names(), 'row_count': len(rows),
            'all_valid': validate_all(rows)}
