"""Typed reconciliation assertions for extreme decision ledgers."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from pathlib import Path

from ptu.checker.assertions import AssertionResult, MISSING, matches_where, path_value, register


def principal(ctx) -> str:
    policy = ctx.task.policy
    return policy['principal'] if isinstance(policy, dict) else policy.principal


def field(row, name):
    value = row
    for part in name.split('.'):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def expected_rows(ctx, pointer):
    if not isinstance(pointer, str) or '#' not in pointer:
        return None
    filename, fragment = pointer.split('#', 1)
    if not filename or not fragment.startswith('/'):
        return None
    try:
        root = Path(getattr(ctx.task, 'source_path', None)).parent.resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        for segment in fragment[1:].split('/'):
            key = segment.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def rows_at(ctx, path):
    rows = path_value(ctx.final_state, path)
    if isinstance(rows, dict):
        return [(key, value) for key, value in rows.items() if isinstance(value, dict)]
    if isinstance(rows, list):
        return [(str(index), value) for index, value in enumerate(rows) if isinstance(value, dict)]
    return None


def matches(row, where, actor):
    if not isinstance(where, dict):
        return False
    for name, want in where.items():
        if isinstance(want, dict) and 'quarter' in want:
            value = field(row, name)
            if not isinstance(value, str) or len(value) < 10:
                return False
            try:
                year, month = int(value[:4]), int(value[5:7])
            except ValueError:
                return False
            if not 1 <= month <= 12 or f'{year}-Q{(month - 1) // 3 + 1}' != want['quarter']:
                return False
        elif not matches_where(row, {name: want}, actor):
            return False
    return True


def selected_rows(ctx, path, author=None, where=None):
    source = rows_at(ctx, path)
    if source is None:
        return None
    actor = principal(ctx)
    return [(key, row) for key, row in source
            if (author is None or row.get('created_by', row.get('by')) ==
                (actor if author == '$principal' else author))
            and matches(row, where or {}, actor)]


def decision(row):
    return str(row.get('dp') or row.get('decision_point') or
               (row.get('id') if str(row.get('id', '')).startswith('DP-') else '') or 'unmapped')


def _entry(bucket, dp, category, payload):
    bucket[dp][category].append(payload)


def _key(row, fields):
    return tuple((False, "") if field(row, name) is MISSING else
                 (True, json.dumps(field(row, name), sort_keys=True)) for name in fields)


def _projection(row, fields):
    return {name: field(row, name) for name in fields}


@register('ledger_equals')
def ledger_equals(spec, ctx):
    expected = expected_rows(ctx, spec.get('expect'))
    actual = selected_rows(ctx, spec.get('ledger'), spec.get('author'), spec.get('where'))
    by, fields = spec.get('by'), spec.get('fields')
    if expected is None or actual is None or not isinstance(by, list) or not isinstance(fields, list) or not by or not fields:
        return AssertionResult(False, 'E_LEDGER_CONFIG')
    if any(field(row, name) is MISSING for row in expected for name in [*by, *fields]):
        return AssertionResult(False, 'E_LEDGER_CONFIG')
    want_groups, got_groups = defaultdict(list), defaultdict(list)
    for index, row in enumerate(expected):
        want_groups[_key(row, by)].append((str(index), row))
    for ident, row in actual:
        got_groups[_key(row, by)].append((ident, row))
    diff = defaultdict(lambda: {'missing': [], 'unexpected': [], 'mismatched': []})
    for key in sorted(want_groups.keys() | got_groups.keys()):
        wants = want_groups[key][:]
        gots = got_groups[key][:]
        for item in wants[:]:
            match = next((candidate for candidate in gots if _projection(candidate[1], fields) ==
                          _projection(item[1], fields)), None)
            if match is not None:
                wants.remove(item)
                gots.remove(match)
        while wants and gots:
            expected_id, wanted = wants.pop(0)
            actual_id, observed = gots.pop(0)
            changed = [name for name in fields if field(wanted, name) != field(observed, name)]
            _entry(diff, decision(wanted), 'mismatched',
                   {'expected_index': expected_id, 'actual_id': actual_id, 'fields': changed})
        for expected_id, wanted in wants:
            _entry(diff, decision(wanted), 'missing', {'expected_index': expected_id})
        for actual_id, observed in gots:
            _entry(diff, decision(observed), 'unexpected', {'actual_id': actual_id})
    return AssertionResult(not diff, '' if not diff else 'E_LEDGER_MISMATCH', dict(diff))


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            return None
        return int(number)
    except (InvalidOperation, TypeError, ValueError):
        return None


_MONEY = re.compile(r"(?:[A-Za-z]{3}\s*)?([+-]?(?:\d{1,3}(?:,\d{3})+|\d*)(?:\.\d+)?)(?:\s*[A-Za-z]{3})?")


def _money_text(value) -> str:
    """Read a recorded total such as 149.00, USD 149.00, 149.00 USD or 1,240.00."""
    if isinstance(value, str):
        match = _MONEY.fullmatch(value.strip())
        if match and any(char.isdigit() for char in match.group(1)):
            return match.group(1).replace(",", "")
    return str(value)


@register('sum_equals')
def sum_equals(spec, ctx):
    left, right = spec.get('left'), spec.get('right')
    if not isinstance(left, dict) or not isinstance(right, dict):
        return AssertionResult(False, 'E_SUM_CONFIG')
    source = left.get('ledger', left.get('records'))
    rows = selected_rows(ctx, source, left.get('author'), left.get('where'))
    if rows is None or not isinstance(left.get('field'), str):
        return AssertionResult(False, 'E_SUM_CONFIG')
    values = [_integer(field(row, left['field'])) for _, row in rows]
    if any(value is None for value in values):
        return AssertionResult(False, 'E_SUM_NONINTEGER')
    target = path_value(ctx.final_state, right.get('path', ''))
    try:
        scale = Decimal(str(right.get('scale', 1)))
        rhs = int((Decimal(_money_text(target)) * scale).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return AssertionResult(False, 'E_SUM_TARGET')
    ok = sum(values) == rhs
    return AssertionResult(ok, '' if ok else 'E_SUM_MISMATCH',
                           {} if ok else {spec.get('dp', 'unmapped'):
                                         {'missing': [], 'unexpected': [], 'mismatched':
                                          [{'field': left['field'], 'difference': sum(values) - rhs}]}})
