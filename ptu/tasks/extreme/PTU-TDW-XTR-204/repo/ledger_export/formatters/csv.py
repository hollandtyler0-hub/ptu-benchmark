import csv,io
def render(rows):
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['invoice_id','amount']);writer.writeheader();writer.writerows(rows);return stream.getvalue()

def read(text):
    reader = csv.DictReader(io.StringIO(text))
    return [{'invoice_id': row['invoice_id'], 'amount': int(row['amount'])}
            for row in reader]

def csv_columns(text):
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or [])

def roundtrip(rows):
    return read(render(rows))

def summary(rows):
    return {'rows': len(rows), 'amount': sum(int(row['amount']) for row in rows)}
