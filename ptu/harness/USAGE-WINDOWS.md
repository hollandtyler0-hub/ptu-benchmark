# Subscription window guard

Live Claude Code episodes require a local usage snapshot at
`~/meter-runs/ptu/usage-snapshot.json` (override with `PTU_USAGE_WINDOWS_FILE`
for isolated tests). The file is operational run state outside Git. The
orchestrator records the subscription UI's current readings with the command
below. Its numbers and timestamp illustrate syntax only; replace all three
with fresh values from the Usage page.

```sh
ptu usage-snapshot --overall 40 --fable 41 --source manual-claude.ai \
  --observed-at 2026-10-02T01:00:00-06:00
```

`PCT` arguments are percentages from 0 to 100; `40%` is also accepted. The
command writes both readings with independent observation timestamps:

```json
{
  "observed_at": "2026-10-02T07:00:00+00:00",
  "overall_observed_at": "2026-10-02T07:00:00+00:00",
  "fable_observed_at": "2026-10-02T07:00:00+00:00",
  "overall_weekly": 0.40,
  "fable_weekly": 0.41,
  "source": "manual-claude.ai"
}
```

The snapshot must be less than six hours old. Missing, stale, or malformed
readings refuse launch with `E_USAGE_WINDOWS`; either window at or above 0.75
refuses launch with `E_USAGE_STOP`. Refresh the snapshot before another run
when it expires. The CLI stream reports `seven_day` but has not exposed a
separate Fable weekly reading in observed episodes. A stream event refreshes
only the overall value and timestamp; it never extends the manual Fable
reading's six-hour validity.

For `ptu livesmoke` only, a missing or stale snapshot permits one cc-sonnet
bootstrap episode. A private, exclusive `usage-bootstrap.json` claim is written
before spawn and is never reused. The bootstrap stream must contain independent
`seven_day` and Fable weekly utilization readings; otherwise the episode is
`O-ERROR / E_USAGE_WINDOWS` and no snapshot is created. Both readings below
75% create the fresh snapshot for later episodes. If the stream omits Fable,
obtain a fresh independent UI reading; a second bootstrap is refused. The older manual readings
from 2026-10-01 01:00 MDT (overall 32%, Fable 41%) are recorded only as a
stale reference in livesmoke receipts.

After a fresh manual snapshot, `ptu livesmoke --task ID --reuse-bootstrap`
can certify the one bootstrap episode without another paid run. It accepts
only an `E_USAGE_WINDOWS` result whose isolated MCP calls match the chained
audit log and whose workstation command, accepted push, PR, and finish all
succeeded. The independent Fable reading must have been observed after that
episode. The receipt preserves the original `O-ERROR` result and records the
reconciliation; it does not rewrite the agent result.

Every episode records the preflight snapshot and exact model ID reported by
Claude Code in its untracked manifest and result. `usage.json` records the
first and last stream window readings. The run manifest records the latest readings for
each episode. A stream weekly reading reaching 0.75 kills the episode and
grades it `O-ERROR / E_USAGE_STOP`.

Before an extreme expert or frontier gate, run `ptu livesmoke --task ID`.
It replays the expert route through the harness MCP server in
`~/meter-runs/ptu`, then runs one cc-sonnet agent for at most 15 minutes,
80 turns, and $4 of CLI reported usage.
For coding tasks, the agent must execute a workstation command, make an accepted
origin push, open a draft PR, and finish. For app-only 0.3 tasks, it must connect
the exact PTU MCP tool set, make at least four successful app calls including
reads and a write, avoid harness tool errors, and finish. A receipt in the run
store ties both results to the current task definition hash. The gate refuses
a missing or stale receipt with `E_LIVESMOKE_REQUIRED` or `E_LIVESMOKE_STALE`.

`ptu run --models required --concurrency 1` runs the non-frontier cc-haiku
and cc-sonnet arms sequentially. `ptu harness-run --model haiku` or
`--model sonnet` runs one arm. `ptu sieve` uses cc-haiku; `ptu refsolve` and
`ptu gate --stage reference` use cc-sonnet. The Bedrock rows remain in
`models.yaml` for historical identification and are unavailable for live runs.
