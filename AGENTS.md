# Agent Guide

Read `README.md`, `docs/AGENT.md`, and `CLAUDE.md` before changing the CLI or
iGPSPORT integration.

- Never print or commit credentials, tokens, `.env` files, private API payloads,
  athlete snapshots, or generated CLI state. Use synthetic fixtures and
  status-only evidence.
- Preserve the existing activity, workout, GUI, Bryton, and legacy `sync-zones`
  contracts unless the task explicitly changes them.
- `sync-rider-settings` is the selective, verified cycling source-of-truth path:
  Intervals.icu owns the values; the client transfers them to iGPSPORT and reads
  them back. It must not analyze, score, prescribe, or estimate rider data.
- Keep `Ride` as the only supported rider-settings sport until generic sport
  behavior is designed and tested. Missing source values must not clear
  iGPSPORT fields.
- Zone recognition is strict and versioned. Preserve the Friel HR semantic union,
  Coggan power half-up conversion, destination power cap, automatic dependencies,
  inactive HR tables, nickname, location, and all unselected profile fields.
  Unknown or malformed models fail closed; do not add generic interpolation.
- Normal and mutating output is status-only. Values may appear only with the
  explicit `--dry-run --show-values` combination.
- Develop test-first and run the focused tests followed by the complete offline
  pytest suite. Confirm formatting/diff checks before committing.
- The immutable upstream revision deployed by the relay is recorded in the
  downstream relay Dockerfile. Update that pin and its documentation deliberately
  when changing the integration contract.
