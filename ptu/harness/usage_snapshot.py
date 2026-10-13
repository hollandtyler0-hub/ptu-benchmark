"""Record an independently observed Claude subscription usage snapshot."""

import argparse
import json

from .usage_windows import check_subscription_windows, record_manual_snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ptu usage-snapshot")
    parser.add_argument("--overall", required=True, metavar="PCT")
    parser.add_argument("--fable", required=True, metavar="PCT")
    parser.add_argument("--source", required=True, choices=["manual-claude.ai"])
    parser.add_argument("--observed-at", required=True, metavar="ISO8601")
    args = parser.parse_args(argv)
    try:
        record_manual_snapshot(args.overall, args.fable, args.source, args.observed_at)
        snapshot = check_subscription_windows()
    except ValueError as error:
        code = str(error)
        print(json.dumps({"code": code if code in {"E_USAGE_WINDOWS", "E_USAGE_STOP"}
                          else "E_USAGE_WINDOWS"}))
        return 1
    print(json.dumps({"observed_at": snapshot["observed_at"],
                      "overall_weekly": snapshot["overall_weekly"],
                      "fable_weekly": snapshot["fable_weekly"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
