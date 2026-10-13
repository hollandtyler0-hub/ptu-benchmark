from ptu.common.content_scan import scan_content


def test_content_scan_accepts_reserved_domains_and_clean_text():
    assert scan_content({"github": {"text": "mail@tidewater.example.com and api.example.net"}}) == []


def test_content_scan_finds_forbidden_markers_domains_and_vendor_patterns():
    docs = {"github": {"text": "PTU-SYN-1 hazard tripwire checker alice@corp.com sk_test_abc"}}
    findings = scan_content(docs)
    assert "PTU-" in findings
    assert "hazard" in findings
    assert "tripwire" in findings
    assert "checker" in findings
    assert "corp.com" in findings
    assert "vendor_pattern" in findings


def test_content_scan_accepts_source_file_paths():
    assert scan_content({"github": {"deploy/config.yaml": "see src/server/routes.ts"}}) == []


def test_content_scan_rejects_internal_hostname_but_not_code_members():
    assert scan_content({"x": "Math.round(value) at api-staging.internal"}) == ["api-staging.internal"]
    assert scan_content({"x": "https://rogue.synthetic"}) == ["rogue.synthetic"]
