import pytest

from ptu.common.refs import ancestors, glob_match, parse_ref, specificity


def test_ref_hierarchy():
    assert parse_ref("github:file/tidewater/web@main:deploy/config.yaml") == (
        "github", "file", "tidewater/web@main:deploy/config.yaml"
    )
    assert ancestors("github:file/tidewater/web@main:deploy/config.yaml") == [
        "github:branch/tidewater/web#main", "github:repo/tidewater/web", "github:org/tidewater"
    ]
    assert ancestors("slack:message/#eng-web/42") == ["slack:channel/#eng-web"]
    assert ancestors("linear:comment/ENG-231/1") == ["linear:issue/ENG-231", "linear:team/ENG"]
    with pytest.raises(ValueError, match="^E_BAD_REF$"):
        parse_ref("bad-ref")


@pytest.mark.parametrize("path,match", [
    ("deploy/.env", True), ("deploy/.env.production", True),
    ("deploy/.env.local", True), ("deploy/env.md", False),
    ("docs/.envrc-notes", False),
])
def test_review_focus_glob(path, match):
    pattern = "github:file/tidewater/web@*:deploy/.env*"
    assert glob_match(pattern, f"github:file/tidewater/web@fix/x:{path}") is match


def test_globs_and_specificity():
    assert glob_match("github:file/**", "github:file/a/b")
    assert not glob_match("github:file/*", "github:file/a/b")
    assert specificity("a*b**c") == 3
