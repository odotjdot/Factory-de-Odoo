# FMOS-807 AI Lab Odoo wrapper

`fm_ai_lab` is a standalone FMOS-authoritative operator/platform addon. It is not
a customer Dashboard module and does not contain an AI runtime. The canonical
FMOS handler remains `FM_AILabHandler`; Odoo only receives structured metadata
projections and emits allowlisted commands through the shared bidirectional seam.

## Contracts

- Provider: `fmos.ai-lab.operator.v1`
- Wrapper: `fmos.ai-lab.wrapper.v1`
- Projection: `fmos.bidirectional.projection.v1`
- Action: `fmos.bidirectional.odoo-command.v1`
- Return authorization: `fmos.bidirectional.return-authorization.v1`

## Metadata boundary

The generated models expose provider readiness, instruction and evaluation prompt
version/hash/status, corpus embedding statistics, evaluation run/output telemetry,
active configuration metadata, job progress, usage, budget controls, model rates,
and append-only audit fingerprints.

The wrapper excludes provider secret references, base URLs, prompt and instruction
bodies, guardrails and rubrics, model output text, corpus documents/chunks,
free-form audit before/after state, actors, claim tokens, operator error messages,
and promotion rationale. Provider creation, instruction/prompt authoring, and corpus
document ingestion remain in the native FMOS operator surface because their inputs
contain secrets or unrestricted content.

## Command routing

The fixed command vocabulary maps to existing FMOS handlers:

- `update_provider` → `repository.updateProvider`
- `queue_eval_run` → `repository.createRun`
- `queue_reembed` → `repository.queueReembed`
- `score_output` → `repository.scoreOutput`
- `verify_output` → `runtimeService.verifyOutput`
- `promote_output` → `repository.promoteOutput`
- `run_job` → `runtimeService.processJob`
- `update_budget_controls` → `repository.setControls`
