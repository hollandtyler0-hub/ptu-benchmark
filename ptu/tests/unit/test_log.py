from ptu.common.log import AuditLog, LogEntry


def test_log_chain_and_tamper(tmp_path):
    log = AuditLog(tmp_path / "log.jsonl")
    first = log.append({"tool": "github.get_repo", "seq": 1})
    second = log.append({"tool": "github.get_issue", "seq": 2})
    assert first["prev_entry_sha256"] == "0" * 64
    assert second["prev_entry_sha256"] == first["entry_sha256"]
    assert log.verify_chain()
    assert AuditLog(tmp_path / "log.jsonl").verify_chain()
    log.entries[0]["tool"] = "github.delete_branch"
    assert not log.verify_chain()


def test_log_entry_shape():
    assert "seq" in LogEntry.__dataclass_fields__
    assert "state_sha256_after" in LogEntry.__dataclass_fields__
