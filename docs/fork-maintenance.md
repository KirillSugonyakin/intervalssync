# Fork preservation and upstream contributions

This fork retains additional behavior for its downstream relay while proposing
smaller, compatible contributions to the original project.

| Branch | Contract |
| --- | --- |
| `codex/runtime-baseline-2026-10-15` | Exact runtime-source baseline `90a8066f45de3e1cf6e29dc17b58ec6c589f533c` |
| `codex/fork-preserved-2026-10-15` | Complete reviewed fork, including provider-aware workout identities and strict rider-zone policy |
| `codex/upstream-workout-refresh` | PR #62: event-ID-based fingerprints, native secondary cadence, verification, and accepted-ID checkpoints |
| `codex/upstream-rider-settings` | PR #63: selective rider sync around upstream's existing zone conversion and HR-mode policy |

The full-feature branch retains `source_identity_key`, `fallback_slot_key`,
`apply_workout_state`, and authoritative `workout_records`. It also retains strict
zone recognition, half-up conversion, fetched power-cap preservation, and the
recognized Friel HR model's LTHR-mode selection. The upstream contribution
branches intentionally have different contracts and must not be substituted for
the runtime branch without migration review.

The reviewed branch adds the following protections relative to the exact
runtime baseline: malformed listing rejection; current-event ownership checks
before fallback; unstructured-event identity retention; GUI identity persistence
and accepted-ID checkpoints; private CLI error suppression; strict zone-row and
rounded-boundary validation; and malformed repetition validation. Shared legacy
sport-settings parsing is preserved while strict parsing remains on the rider
path. The source/calendar dataclass preserves legacy positional compatibility.

Before a future upstream update, create an integration branch and apply the
provider-identity and strict-zone extensions as explicit fork-only changes. Test
existing state, pending verification, skip markers, identity conflicts, and the
intentionally different zone outputs. Passing upstream CI alone does not prove
the fork's contracts remain intact. Release/deployment changes are separate
operations; this branch-preservation work changes no production pin.
