"""Lint entry point for the export package."""

import ast
import sys
from pathlib import Path


def main():
    if sys.argv[1:] != ['check', 'ledger_export']:
        return 2
    for source in sorted(Path('ledger_export').rglob('*.py')):
        text = source.read_text()
        try:
            ast.parse(text, filename=str(source))
        except SyntaxError:
            return 1
        if any(line.rstrip() != line for line in text.splitlines()):
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
