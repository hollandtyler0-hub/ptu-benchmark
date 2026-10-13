"""The workstation process is isolated even when its command is hostile."""

import os
import sys
import tempfile
import subprocess
from pathlib import Path

import pytest

from ptu.sandbox import runner, toolchain


@pytest.fixture
def workstation():
    parent = Path.home() / ".meter-harness" / "isolation-tests"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        root = Path(directory) / "episode" / "ws"
        (root / "work").mkdir(parents=True)
        yield root


@pytest.fixture
def executable():
    if not runner.available():
        pytest.skip("E_SANDBOX_UNAVAILABLE")
    return str(toolchain.build("py314-stdlib"))


def test_minimal_environment_and_null_stdin(workstation, executable, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "invented-canary")
    monkeypatch.setenv("PTU_TOKEN", "invented-canary")
    result = runner.run("python3 -c 'import os,sys,json; print(json.dumps(sorted(os.environ))); print(sys.stdin.read())'",
                        ws_root=workstation, toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code == 0
    assert "invented-canary" not in result.output
    assert "AWS" not in result.output and "TOKEN" not in result.output
    assert "HOME" in result.output and "PYTHONHASHSEED" in result.output
    assert "CODEX" not in result.output and "CLAUDE" not in result.output


def test_env_attack_discriminates(workstation, executable, monkeypatch):
    monkeypatch.setenv("PTU_TOKEN", "invented-canary")
    control = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('PTU_TOKEN', ''))"],
                             capture_output=True, text=True)
    result = runner.run("python3 -c 'import os; print(os.environ.get(\"PTU_TOKEN\", \"\"))'",
                        ws_root=workstation, toolchain=executable, timeout_s=10, limits={})
    assert control.stdout.strip() == "invented-canary"
    assert result.exit_code == 0 and result.output.strip() == ""


def test_output_cap_keeps_full_copy_inside_ws(workstation, executable):
    result = runner.run("python3 -c 'print(\"x\" * 200)'", ws_root=workstation,
                        toolchain=executable, timeout_s=10, limits={"max_output_bytes": 40})
    assert result.exit_code == 0 and result.truncated
    assert "[output truncated" in result.output
    assert result.full_output_path
    full = workstation / result.full_output_path
    assert full.is_file() and full.read_text().startswith("x" * 100)


def test_timeout_and_background_processes_die(workstation, executable):
    result = runner.run("sleep 300", ws_root=workstation, toolchain=executable,
                        timeout_s=1, limits={})
    assert result.timed_out
    with pytest.raises(ValueError, match="E_WS_SHELL_UNSUPPORTED"):
        runner.run("sleep 300 & echo $! > child.pid", ws_root=workstation,
                   toolchain=executable, timeout_s=10, limits={})
    assert not (workstation / "work" / "child.pid").exists()
    result = runner.run("python3 -c 'import os; os.fork()'", ws_root=workstation,
                        toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code != 0


def test_detached_child_cannot_escape(workstation, executable):
    result = runner.run("python3 -c 'import os; os.posix_spawn(\"/bin/sleep\", [\"sleep\", \"30\"], {})'",
                        ws_root=workstation, toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code != 0


def test_stdlib_and_git_work(workstation, executable):
    (workstation / "work" / "test_smoke.py").write_text(
        "import unittest\nclass Smoke(unittest.TestCase):\n"
        "    def test_ok(self): self.assertTrue(True)\n")
    command = "python3 -m unittest && git init -q && git -c user.name=Test -c user.email=test@example.invalid commit --allow-empty -qm initial"
    result = runner.run(command, ws_root=workstation, toolchain=executable,
                        timeout_s=20, limits={})
    assert result.exit_code == 0


def test_pinned_python_can_read_system_timezone_data(workstation, executable):
    result = runner.run(
        "python3 -S -c 'from zoneinfo import ZoneInfo; assert ZoneInfo(\"America/Denver\").key == \"America/Denver\"'",
        ws_root=workstation, toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code == 0


def test_shell_glob_is_preserved(workstation, executable):
    (workstation / "work" / "a.txt").write_text("A")
    (workstation / "work" / "b.txt").write_text("B")
    result = runner.run("echo *.txt", ws_root=workstation,
                        toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code == 0
    assert result.output.strip() == "a.txt b.txt"


def test_sequential_shell_and_supported_text_commands(workstation, executable):
    (workstation / "work" / "sample.txt").write_text("alpha\nbeta\n")
    result = runner.run("pwd; grep beta sample.txt 2>&1; head -n 1 sample.txt; sed -n '2p' sample.txt",
                        ws_root=workstation, toolchain=executable, timeout_s=10, limits={})
    assert result.exit_code == 0
    assert str(workstation.parent) not in result.output
    assert "beta" in result.output and "alpha" in result.output
    with pytest.raises(ValueError, match="E_WS_SHELL_UNSUPPORTED"):
        runner.run("cat sample.txt | head", ws_root=workstation,
                   toolchain=executable, timeout_s=10, limits={})


def test_invalid_cwd_fails_closed(workstation, executable):
    with pytest.raises(ValueError, match="E_WS_PATH"):
        runner.run("pwd", ws_root=workstation, cwd="../private", toolchain=executable,
                   timeout_s=10, limits={})


def test_root_outside_home_fails_closed(tmp_path, executable):
    root = tmp_path / "ws"
    (root / "work").mkdir(parents=True)
    with pytest.raises(ValueError, match="E_SANDBOX_PATH"):
        runner.run("echo hello", ws_root=root, toolchain=executable,
                   timeout_s=10, limits={})


def test_output_path_swap_cannot_make_host_read_private_file(workstation, executable):
    private = workstation.parent / "private"
    private.mkdir()
    target = private / "task.yaml"
    target.write_text("invented-canary")
    code = ("from pathlib import Path; "
            "p=next(Path('../tmp').glob('exec-*.output')); "
            f"p.unlink(); p.symlink_to({str(target)!r})")
    import shlex
    with pytest.raises(ValueError, match="^E_WS_OUTPUT_TAMPER$"):
        runner.run("python3 -c " + shlex.quote(code), ws_root=workstation,
                   toolchain=executable, timeout_s=10, limits={})
