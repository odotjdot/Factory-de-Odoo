"""FMOS-807 AI Lab thin-wrapper contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from amil_utils.odoo_bridge import parse_thin_bridge_spec, render_thin_bridge_addon

AI_LAB_SPEC = Path(__file__).parents[1] / "specs" / "fmos807" / "fm_ai_lab.json"


def test_ai_lab_wrapper_projects_exact_operator_metadata_and_actions() -> None:
    raw = json.loads(AI_LAB_SPEC.read_text(encoding="utf-8"))
    spec = parse_thin_bridge_spec(raw)
    generated = render_thin_bridge_addon(spec)
    files = generated.file_map()
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    projections = files["models/projections.py"].decode()

    assert spec.module_name == "fm_ai_lab"
    assert spec.provider_contract_id == "fmos.ai-lab.operator.v1"
    assert contract["contracts"] == {
        "provider": "fmos.ai-lab.operator.v1",
        "wrapper": "fmos.ai-lab.wrapper.v1",
        "projection": "fmos.bidirectional.projection.v1",
        "action": "fmos.bidirectional.odoo-command.v1",
        "return_authorization": "fmos.bidirectional.return-authorization.v1",
    }
    assert contract["allowlisted_actions"] == {
        "promote_output": "ai-lab.eval-output.promote.requested.v1",
        "queue_eval_run": "ai-lab.eval-run.queue.requested.v1",
        "queue_reembed": "ai-lab.corpus.reembed.requested.v1",
        "run_job": "ai-lab.job.run.requested.v1",
        "score_output": "ai-lab.eval-output.score.requested.v1",
        "update_budget_controls": "ai-lab.model-controls.update.requested.v1",
        "update_provider": "ai-lab.provider.update.requested.v1",
        "verify_output": "ai-lab.eval-output.verify.requested.v1",
    }
    assert set(contract["models"]) >= {
        "fm.ai.lab.provider",
        "fm.ai.lab.instruction",
        "fm.ai.lab.eval.prompt",
        "fm.ai.lab.eval.run",
        "fm.ai.lab.eval.output",
        "fm.ai.lab.corpus",
        "fm.ai.lab.job",
        "fm.ai.lab.active.config",
        "fm.ai.lab.usage",
        "fm.ai.lab.budget.control",
        "fm.ai.lab.model.rate",
        "fm.ai.lab.audit.receipt",
        "fm.ai.lab.crosswalk",
        "fm.ai.lab.projection.receipt",
        "fm.ai.lab.action.receipt",
        "fm.ai.lab.command.outbox",
    }
    for forbidden in (
        "secret_ref",
        "system_prompt",
        "user_message",
        "output_text",
        "document_content",
        "chunk_content",
        "before_state",
        "after_state",
        "actor_id = fields.",
        "claim_token",
        "last_error_message",
        "promotion_reason = fields.",
    ):
        assert forbidden not in projections
    tests = files["tests/test_protocol.py"].decode()
    assert "test_duplicate_action_delivery_is_idempotent" in tests
    assert "test_action_fingerprint_conflict_fails_closed" in tests
    assert "test_timeout_recovery_fences_stale_worker" in tests
    assert "test_two_company_isolation" in tests
