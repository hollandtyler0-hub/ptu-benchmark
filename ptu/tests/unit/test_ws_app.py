from pathlib import Path
import tempfile
from dataclasses import replace

import pytest
import ptu.apps

from ptu.apps.ws import ops
from ptu.apps.ws.app import _specs
from ptu.harness.mcp_client import registry_for, tool_specs
from ptu.tasks.loader import load_task


@pytest.fixture
def root():
    parent = Path.home() / ".meter-harness" / "isolation-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        ws = Path(directory) / "episode" / "ws"
        (ws / "work").mkdir(parents=True)
        yield ws


def test_write_read_edit_and_unique_match(root):
    ops.write_file(root, "work/a.txt", "one\none\n")
    assert ops.read_file(root, "a.txt")["content"] == "one\none\n"
    with pytest.raises(ValueError, match="E_WS_NOT_FOUND"):
        ops.read_file(root, "docs/missing.md")
    assert ops.read_file(root, "work/a.txt", 1, 1)["content"] == "one\n"
    with pytest.raises(ValueError, match="E_WS_NON_UNIQUE"):
        ops.edit_file(root, "work/a.txt", "one", "two")
    assert ops.edit_file(root, "work/a.txt", "one", "two", True)["replacements"] == 2
    assert ops.read_file(root, "work/a.txt")["content"] == "two\ntwo\n"


def test_paths_cannot_escape_even_by_symlink(root):
    outside = root.parent / "secret"
    outside.write_text("invented-canary")
    (root / "work" / "link").symlink_to(outside)
    for path in ("../secret", str(outside), "link"):
        with pytest.raises(ValueError, match="E_WS_PATH|E_WS_SYMLINK"):
            ops.read_file(root, path)
        with pytest.raises(ValueError, match="E_WS_PATH|E_WS_SYMLINK"):
            ops.write_file(root, path, "changed")
    assert outside.read_text() == "invented-canary"


def test_host_file_tools_refuse_hardlinks(root):
    outside = root.parent / "secret"
    outside.write_text("invented-canary")
    link = root / "work" / "hardlink"
    __import__("os").link(outside, link)
    with pytest.raises(ValueError, match="E_WS_PATH"):
        ops.read_file(root, "hardlink")
    with pytest.raises(ValueError, match="E_WS_PATH"):
        ops.write_file(root, "hardlink", "changed")
    with pytest.raises(ValueError, match="E_WS_PATH"):
        ops.edit_file(root, "hardlink", "invented", "changed")
    assert outside.read_text() == "invented-canary"


def test_host_file_tools_refuse_fifo_without_blocking(root):
    import os
    pipe = root / "work" / "pipe"
    os.mkfifo(pipe)
    for operation in (
        lambda: ops.read_file(root, "pipe"),
        lambda: ops.write_file(root, "pipe", "x"),
        lambda: ops.edit_file(root, "pipe", "x", "y"),
    ):
        with pytest.raises(ValueError, match="^E_WS_"):
            operation()


def test_all_tools_log_ws_resources(root):
    specs = {spec.name: spec for spec in _specs(None, {"root": root})}
    assert set(specs) == {"ws.exec", "ws.read_file", "ws.write_file", "ws.edit_file"}
    assert specs["ws.exec"].resources_fn({"command": "true"}, {})[0].ref == "ws:exec"
    for name in ("read_file", "write_file", "edit_file"):
        assert specs["ws." + name].resources_fn({"path": "work/a"}, {})[0].ref == "ws:file/work/a"
        assert "work/" in specs["ws." + name].description
    assert "git push -u origin HEAD" in specs["ws.exec"].description


def test_registry_loads_ws_only_for_declared_workstation(monkeypatch):
    fixture_apps = Path(__file__).parents[1] / "fixtures" / "apps"
    monkeypatch.setattr(ptu.apps, "__path__", [*ptu.apps.__path__, str(fixture_apps)])
    task = load_task(Path(__file__).parents[1] / "fixtures" / "extreme_min" / "task.yaml")
    with_ws = registry_for(task)
    with_names = {row["toolSpec"]["name"] for row in tool_specs(with_ws, task)}
    assert {"ws__exec", "ws__read_file", "ws__write_file", "ws__edit_file"} <= with_names
    assert "ws" in with_ws.apps

    without_task = replace(task, workstation=None, services=["notes", "control"])
    without_ws = registry_for(without_task)
    without_names = {row["toolSpec"]["name"] for row in tool_specs(without_ws, without_task)}
    assert without_names == with_names - {"ws__exec", "ws__read_file", "ws__write_file", "ws__edit_file"}
    assert "ws" not in without_ws.apps


def test_episode_task_binds_ws_to_its_sibling_workstation(root, monkeypatch):
    fixture_apps = Path(__file__).parents[1] / "fixtures" / "apps"
    monkeypatch.setattr(ptu.apps, "__path__", [*ptu.apps.__path__, str(fixture_apps)])
    private = root.parent / "private"
    private.mkdir()
    task_file = private / "task.yaml"
    task_file.write_bytes((Path(__file__).parents[1] / "fixtures" / "extreme_min" / "task.yaml").read_bytes())
    task = load_task(task_file)
    registry = registry_for(task)
    written = registry.get("ws.write_file").handler({"path": "work/episode.txt", "content": "bound"}, {}, None, None)
    assert written["path"] == "work/episode.txt"
    assert (root / "work" / "episode.txt").read_text() == "bound"
