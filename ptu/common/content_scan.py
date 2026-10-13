"""Reject task leakage and real-world domains in synthetic workspace content."""

import json
import re


_FORBIDDEN = ("PTU-", "hazard", "tripwire", "checker")
_DOMAIN = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
_VENDOR = re.compile(r"\b(?:ghp_[A-Za-z0-9]{3,}|github_pat_[A-Za-z0-9_]{3,}|sk_(?:test|live|proj)_[A-Za-z0-9]{3,}|xox[baprs]-[A-Za-z0-9-]{3,}|AKIA[A-Z0-9]{12,}|AIza[A-Za-z0-9_-]{10,})\b")
_SOURCE_SUFFIXES = {"ts", "tsx", "py", "json", "yaml", "yml", "toml", "md", "css", "html", "sql", "sh"}
_DOMAIN_SUFFIXES = {"com", "net", "org", "io", "ai", "dev", "app", "co", "biz", "info",
                    "edu", "gov", "cloud", "tech", "internal", "local", "test", "invalid"}
_EXPLICIT_HOST = re.compile(r"(?:https?://|@)([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def scan_content(state_docs: dict) -> list[str]:
    text = json.dumps(state_docs, ensure_ascii=False, sort_keys=True)
    findings = [word for word in _FORBIDDEN if word.lower() in text.lower()]
    explicit_hosts = set(_EXPLICIT_HOST.findall(text))
    for domain in _DOMAIN.findall(text):
        # File paths such as src/server/routes.ts are not network domains.
        suffix = domain.rsplit(".", 1)[-1].lower()
        if suffix in _SOURCE_SUFFIXES or (suffix not in _DOMAIN_SUFFIXES and domain not in explicit_hosts):
            continue
        if not (domain in {"example.com", "example.net", "example.org"} or
                domain.endswith((".example.com", ".example.net", ".example.org", ".test", ".invalid")) or
                domain.endswith((".test", ".invalid"))):
            findings.append(domain)
    if _VENDOR.search(text):
        findings.append("vendor_pattern")
    return sorted(set(findings))
