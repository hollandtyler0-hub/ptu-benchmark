from dataclasses import fields

import pytest

from ptu.apps import app_ids, load_apps
from ptu.apps.base import Actor, AppError, AppSpec


def test_app_contract_and_unknown_id():
    assert [field.name for field in fields(Actor)] == ["principal", "acting_for", "role", "teams"]
    assert [field.name for field in fields(AppError)] == ["code", "message", "kind", "field"]
    assert [field.name for field in fields(AppSpec)] == [
        "id", "reference_product", "fidelity_version", "surfaces", "report_unit",
        "tool_specs", "authorize", "refs_for_path", "ancestors", "bookkeeping",
        "created_ref", "external_check", "event_ops", "unread",
    ]
    assert isinstance(app_ids(), list)
    with pytest.raises(ValueError, match="^E_APP_UNKNOWN$"):
        load_apps(["absent"])
