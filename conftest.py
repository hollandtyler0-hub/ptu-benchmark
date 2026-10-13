"""Public-tree pytest configuration.

Two tests assert properties of private build records that are not published
with this release: an internal draft scorecard and the private review-record
headers. They are deselected only when those records are absent, and pytest
reports them as deselected.
"""

from pathlib import Path


_PRIVATE_RECORD_TESTS = {
    "ptu/tests/unit/test_scorecard.py::test_committed_scorecard_is_explicitly_pending":
        "ptu/scorecards/2026-10-06-demo-v0.1.md",
    "ptu/tests/unit/test_scorecard_extreme.py::test_reviewed_expert_headers_preserve_void_and_corrected_counts":
        "plans/reviews",
}


def pytest_collection_modifyitems(config, items):
    root = Path(__file__).resolve().parent
    kept, deselected = [], []
    for item in items:
        record = _PRIVATE_RECORD_TESTS.get(item.nodeid)
        (deselected if record and not (root / record).exists() else kept).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
