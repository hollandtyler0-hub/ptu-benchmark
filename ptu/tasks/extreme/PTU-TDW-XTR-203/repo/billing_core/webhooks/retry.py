def next_delay(attempt):
    return min(60, 2 ** max(0, attempt))

def retry_schedule(attempts):
    return [next_delay(index) for index in range(attempts)]

def should_retry(status, attempt, maximum=6):
    return attempt < maximum and (status >= 500 or status == 429)

def retry_record(event_id, attempt, status):
    return {'event_id': event_id, 'attempt': attempt, 'status': status,
            'next_delay': next_delay(attempt) if should_retry(status, attempt) else None}

def exhausted(record, maximum=6):
    return record['attempt'] >= maximum
