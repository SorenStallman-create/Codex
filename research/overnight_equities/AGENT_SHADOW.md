# Research agent decision interface

This is the first implementation step for the research-driven agent in
[`CODEX_HANDOFF.md`](../../CODEX_HANDOFF.md). It defines a provider-neutral,
paper-only decision record and an append-only, hash-chained JSONL log. The
current implementation validates and records decisions. It does not call a
model, fetch data, alter the frozen strategy, evaluate outcomes, write paper
fills, or submit orders.

## Frozen run contract

Each preregistered shadow run uses one `run_id` and one fixed agent identity:
`model_id`, `model_version`, `prompt_sha256`, and `config_sha256`. The log
refuses a second decision for the same run and session, a changed agent identity
within a run, non-increasing session dates, and any edit that breaks the event
hash chain. Corrections require a new, separately defined event type rather than
rewriting an earlier decision.

One decision record includes:

- session date, decision time, data cutoff, and a hash of the full input snapshot;
- named data sources, their observation times, and content hashes;
- a `candidate` or `no_trade` outcome, with sourced evidence, confidence,
  risk flags, and invalidation conditions;
- the fixed model, prompt, and configuration identifiers for the run.

Only source names and hashes are stored, not raw market/news inputs or secrets.
Every evidence item must name a source in the same decision, and no source may
be newer than the declared data cutoff.

## Record and verify

Prepare one JSON decision object using the fields above, then run:

```sh
python3 research_agent.py record --input /path/to/decision.json
python3 research_agent.py verify
```

The default event log is `state/agent_shadow/events.jsonl`, which is ignored by
Git. Use `--ledger PATH` to select a separate local log. A run should use a
fixed `run_id` and preregistered model identity for its entire shadow window.
If validation fails, no event is appended. Verification checks every event's
schema, sequence, duplicate key, frozen agent identity, increasing session
order, previous-event link, and SHA-256 event hash.

Example shape (replace placeholder values with actual hashes and timestamps):

```json
{
  "run_id": "agent_shadow_20260922_v1",
  "session_date": "2026-09-22",
  "decision_at_utc": "2026-09-22T20:05:00Z",
  "data_asof_utc": "2026-09-22T20:00:00Z",
  "input_snapshot_sha256": "<64 lowercase hex characters>",
  "data_sources": [
    {
      "name": "market_snapshot",
      "observed_at_utc": "2026-09-22T20:00:00Z",
      "sha256": "<64 lowercase hex characters>"
    }
  ],
  "agent": {
    "model_id": "provider/model",
    "model_version": "pinned-version",
    "prompt_sha256": "<64 lowercase hex characters>",
    "config_sha256": "<64 lowercase hex characters>"
  },
  "action": "candidate",
  "symbol": "AAPL",
  "thesis": "Short explanation grounded in the timestamped inputs.",
  "evidence": [
    {"claim": "Evidence summary", "source_name": "market_snapshot"}
  ],
  "confidence": 0.5,
  "risk_flags": ["Example risk to review"],
  "invalidation_conditions": ["Specific condition that would invalidate the thesis"],
  "no_trade_reason": ""
}
```

For `no_trade`, set `action` to `no_trade`, leave `symbol` empty, and provide a
`no_trade_reason`. Confidence remains on the zero-to-one scale. Candidate
decisions require at least one evidence item and one invalidation condition.

## Remaining implementation

Before collecting shadow decisions, create a run-specific preregistration that
freezes the candidate universe, data sources, model/prompt/config hashes,
decision schedule, outcome metrics, comparison cohort, and stop conditions.
Then add the model/data adapter and a separate outcome/review event path. Keep
the deterministic execution controls outside the model. The current paper fill
ledger remains at zero sessions, and the agent has no recorded decisions or
outcomes yet.
