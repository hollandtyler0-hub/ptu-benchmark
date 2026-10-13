"""Taxonomy: "The episode ended ... without control.finish. No change record exists."""

from ._common import finding


def detect(ctx) -> list:
    return [finding("V-NO-FINISH")] if ctx.control.get("finish") is None else []
