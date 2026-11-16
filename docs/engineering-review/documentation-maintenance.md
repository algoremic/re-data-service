# Documentation maintenance and handover

This is a proposed ongoing process, not an automation or an already assigned schedule. Every service needs an accountable owner and backup; assign people to the roles in the roadmap.

## Source of truth

Keep the service LLD, generated API schema, relevant concepts/math guide, operation runbook and test evidence beside its implementation. Keep the system HLD, cross-service LLD, release cohort and aggregate assessment in re-platform. This review's platform bundle is an immutable cross-repository snapshot; future service documents and release cohorts must update deliberately rather than silently alter old findings.

Each document should retain: last reviewed date, source commit/cohort, owner role, implemented behavior, proposed changes, validation command/results, and unknowns. Preserve snapshot-pinned evidence links even when adding new ones. Do not equate “reviewed” with a line-by-line audit or a production certification.

## Change checklist

- API change: regenerate OpenAPI from the provider without secrets or live side effects, document real custom errors, update request/response examples, consumer types and contract tests, record compatibility/versioning.
- Business-rule change: update the rule specification and independent golden examples before treating output as authoritative; version saved-result semantics.
- ML change: identify data/schema/feature/baseline/label/split/model/calibration/policy versions; update formulas and model card; attach out-of-time metrics and release/rollback evidence.
- AI change: version prompts/tools/guardrails/model configuration and adversarial evaluation corpus; inspect streaming behavior and citation/numeric correctness.
- Runtime change: update all overlays, example configuration, target gates, permissions/mounts, health/readiness, HLD and recovery procedure.
- Security or dependency change: update threat boundaries, required claims/capabilities, lockfiles and reproducible image evidence; plan coordinated rotation/migration.

In a release review, verify docs against the exact cohort, exercise examples in a disposable environment, check source links, validate Mermaid and schemas, and record unresolved issues with owners. Review on every material change and choose a regular ownership review interval appropriate to the team; no reminder has been scheduled by this task.

## ADR template

```markdown
# ADR-NNN: <decision>
Status: proposed | accepted | superseded
Date: YYYY-MM-DD
Owner / reviewers: <roles or assigned people>
Applies to: <repository commits/cohort/schema versions>

## Context
Concrete problem, constraints, evidence and product requirement.

## Options considered
Actual alternatives and tradeoffs; do not invent historical rationale.

## Decision
Chosen behavior, boundaries, assumptions and scope.

## Consequences
Correctness/security/latency/cost/operational implications and risks.

## Verification and rollout
Acceptance tests, measurements, migrations, compatibility and rollback.

## Supersedes / links
Related ADRs, LLD/API/math/runbook and tracked work.
```

## Model/evaluation record template

```markdown
# Model or served-policy record: <immutable ID>
Owner; reviewed date; purpose; supported/unsupported decisions and populations.
Data provenance/licence/window; unit/schema/config fingerprint; rejection audit.
Features; target definition and availability; calendar policy; leakage controls.
Candidate algorithms; baseline; search budget; locked temporal splits.
Train/calibration/test sizes; out-of-time MAE/RMSE/bias; paired baseline deltas.
Coverage/width by horizon/segment; confidence meaning; missing/thin/fallback policy.
Selection uncertainty; drift/monitoring thresholds; known limits and failure cases.
Artifact/image/source hashes; release authority; activation; rollback; retirement.
Evidence locations and reproducible commands; independent reviewer acceptance.
```

## Incident/handover record template

Record UTC incident times plus user-facing local time when relevant, impact, exact cohort/data/model IDs, sanitized request IDs, expected versus observed behavior, mitigation, recovery proof, lost/replayed work, root cause, regression check, owner and follow-up date. Never paste secrets or user chat/private data into general documentation.
