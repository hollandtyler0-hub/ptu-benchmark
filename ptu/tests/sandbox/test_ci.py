"""CI reads workflow content from the immutable pushed commit."""

import subprocess
import tempfile
import os
from pathlib import Path

import pytest

from ptu.sandbox import runner, toolchain
from ptu.sandbox.ci import run_checks, _stable_log


def test_ci_log_drops_only_unittest_elapsed_time():
    first = "/ci-runs/tmpabcdef12/ws/work/test.py\nRan 9 tests in 0.004s\nFAILED (failures=1)\n"
    second = "/ci-runs/tmpzyxw9876/ws/work/test.py\nRan 9 tests in 0.019s\nFAILED (failures=1)\n"
    assert _stable_log(first) == _stable_log(second)
    assert "FAILED (failures=1)" in _stable_log(first)


def test_ci_seed_is_attempt_and_private_run_counters_are_not_exported():
    from ptu.sandbox.ci import _env_prefix, _job_environment
    assert _job_environment(4, 2) == {"PYTHONHASHSEED": "2"}
    assert _job_environment(4, 1) == {"PYTHONHASHSEED": "1"}
    schedule = {"first_run_first_attempt": 1, "otherwise": 2}
    for run in range(1, 6):
        for attempt in (1, 2):
            expected = "1" if (run, attempt) == (1, 1) else "2"
            assert _job_environment(run, attempt, ci_seed_schedule=schedule) == {
                "PYTHONHASHSEED": expected}
    for name in ("PTU_CI_RUN_NUMBER", "PTU_CI_ATTEMPT"):
        with pytest.raises(ValueError, match="E_CI_WORKFLOW"):
            _env_prefix({name: "1"})


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.mark.skipif(not runner.available(), reason="E_SANDBOX_UNAVAILABLE")
def test_ci_uses_pushed_commit_and_fresh_sandbox():
    base = Path.home() / ".meter-harness" / "ci-tests"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as name:
        root = Path(name)
        work = root / "work"
        work.mkdir()
        _git("init", "-q", cwd=work)
        workflow = work / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: CI\non: push\njobs:\n  test:\n    name: test suite\n    steps:\n"
                            "      - run: python3 -c 'import os; assert \"PTU_CI_RUN_NUMBER\" not in os.environ; "
                            "assert \"PTU_CI_ATTEMPT\" not in os.environ; "
                            "assert os.environ[\"PYTHONHASHSEED\"] == \"1\"; print(\"PASS\")'\n")
        _git("add", ".", cwd=work)
        _git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "ci", cwd=work)
        sha = _git("rev-parse", "HEAD", cwd=work)
        bare = root / "remote.git"
        _git("clone", "--bare", "-q", str(work), str(bare), cwd=root)
        workflow.write_text("jobs: {}\n")
        checks = run_checks(bare, sha, run_number=1, attempt=1, toolchain=str(toolchain.build("py314-stdlib")))
        assert len(checks) == 1
        assert checks[0]["name"] == "test suite"
        assert checks[0]["conclusion"] == "success"
        assert checks[0]["attempt"] == 1
        assert "PASS" in checks[0]["log"]


@pytest.mark.skipif(not runner.available(), reason="E_SANDBOX_UNAVAILABLE")
def test_rerun_same_commit_changes_attempt():
    base = Path.home() / ".meter-harness" / "ci-tests"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as name:
        root = Path(name)
        work = root / "work"
        work.mkdir()
        _git("init", "-q", cwd=work)
        workflow = work / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True)
        expected = subprocess.run([str(toolchain.build("py314-stdlib") / "bin" / "python3"),
                                   "-c", "print(hash('flaky'))"],
                                  env={"PYTHONHASHSEED": "2", "PATH": "/usr/bin:/bin"},
                                  check=True, capture_output=True, text=True).stdout.strip()
        workflow.write_text("jobs:\n  test:\n    steps:\n"
                            f"      - run: python3 -c 'assert hash(\"flaky\") == {expected}'\n")
        _git("add", ".", cwd=work)
        _git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "ci", cwd=work)
        sha = _git("rev-parse", "HEAD", cwd=work)
        bare = root / "remote.git"
        _git("clone", "--bare", "-q", str(work), str(bare), cwd=root)
        chain = str(toolchain.build("py314-stdlib"))
        one = run_checks(bare, sha, run_number=1, attempt=1, toolchain=chain)
        two = run_checks(bare, sha, run_number=1, attempt=2, toolchain=chain)
        assert one[0]["conclusion"] == "failure"
        assert two[0]["conclusion"] == "success"
        assert one[0]["attempt"] == 1 and two[0]["attempt"] == 2
