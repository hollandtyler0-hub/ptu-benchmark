"""Discovery of installed source-free product applications."""

import importlib
import importlib.util
import os
import pkgutil
import re

from .base import AppSpec


_REGISTERED: dict[str, AppSpec] = {}
if os.environ.get("PTU_APPS_PATH"):
    __path__.append(os.environ["PTU_APPS_PATH"])


def register_apps(apps: dict[str, AppSpec]) -> None:
    _REGISTERED.update(apps)


def registered_app(app_id: str) -> AppSpec | None:
    return _REGISTERED.get(app_id)


def app_ids() -> list[str]:
    return sorted(module.name for module in pkgutil.iter_modules(__path__)
                  if module.ispkg and module.name != "__pycache__"
                  and importlib.util.find_spec(f"ptu.apps.{module.name}.app") is not None)


def load_apps(ids) -> dict[str, AppSpec]:
    available = set(app_ids())
    loaded = {}
    for app_id in ids:
        if not isinstance(app_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", app_id) or app_id not in available:
            raise ValueError("E_APP_UNKNOWN")
        try:
            app = importlib.import_module(f"ptu.apps.{app_id}.app").APP
        except (AttributeError, ImportError):
            raise ValueError("E_APP_UNKNOWN") from None
        if not isinstance(app, AppSpec) or app.id != app_id:
            raise ValueError("E_APP_UNKNOWN")
        loaded[app_id] = app
    register_apps(loaded)
    return loaded
