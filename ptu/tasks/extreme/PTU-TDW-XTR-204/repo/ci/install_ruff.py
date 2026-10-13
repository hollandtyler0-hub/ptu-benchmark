"""Install the pinned synthetic lint fixture into this CI workspace."""

from pathlib import Path

target = Path('.ci-deps')
target.mkdir(exist_ok=True)
(target / 'ruff.py').write_bytes((Path(__file__).parent / 'ruff.py').read_bytes())
