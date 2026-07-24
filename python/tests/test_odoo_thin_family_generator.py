"""Contract tests for generated modular FMOS-authoritative Odoo addons."""

from __future__ import annotations

import ast
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pydantic import ValidationError

from amil_utils.odoo_bridge import parse_thin_bridge_spec, render_thin_bridge_addon

FMOS806_SPECS = Path(__file__).parents[1] / "specs" / "fmos806"
INCPROS_MODULES = (
    "fm_incpros_base",
    "fm_incpros_entities",
    "fm_incpros_intakes",
    "fm_incpros_matters",
)
EXPECTED_INCPROS_MODELS = {
    "fm_incpros_base": [
        "fm.incpros.crosswalk",
        "fm.incpros.projection.receipt",
        "fm.incpros.action.receipt",
        "fm.incpros.command.outbox",
    ],
    "fm_incpros_entities": [
        "fm.incpros.entity",
        "fm.incpros.entity.document",
        "fm.incpros.compliance.obligation",
    ],
    "fm_incpros_intakes": ["fm.incpros.intake"],
    "fm_incpros_matters": [
        "fm.incpros.matter",
        "fm.incpros.matter.event",
        "fm.incpros.matter.document",
    ],
}


def _base_spec() -> dict:
    return {
        "schema_version": "2.0",
        "module_name": "fm_incpros_base",
        "module_title": "FMOS IncPros Protocol",
        "version": "19.0.1.0.0",
        "author": "Funk Media",
        "license": "LGPL-3",
        "provider_contract_id": "fmos.incpros.customer.v1",
        "wrapper_contract_id": "fmos.incpros.wrapper.v1",
        "projection_contract_id": "fmos.bidirectional.projection.v1",
        "action_contract_id": "fmos.bidirectional.odoo-command.v1",
        "protocol_model_prefix": "fm.incpros",
        "role": "protocol_base",
        "base_module": None,
        "dependencies": [],
        "concepts": [],
        "protocol_actions": [
            {
                "aggregate_model": "fm.incpros.entity",
                "name": "transition",
                "label": "Request transition",
                "command_type": "incpros.entity.transition.requested.v1",
                "payload_fields": ["target_status", "reason_code"],
            }
        ],
        "max_attempts": 8,
        "retry_base_seconds": 60,
        "retry_cap_seconds": 3600,
        "cron_batch_size": 100,
    }


def _projection_spec() -> dict:
    return {
        **_base_spec(),
        "module_name": "fm_incpros_entities",
        "module_title": "FMOS IncPros Entities",
        "role": "projection",
        "base_module": "fm_incpros_base",
        "protocol_actions": [],
        "concepts": [
            {
                "name": "entity",
                "title": "IncPros Entity",
                "model_name": "fm.incpros.entity",
                "fields": [
                    {
                        "name": "entity_form",
                        "label": "Entity form",
                        "field_type": "Char",
                        "required": True,
                        "index": True,
                    },
                    {
                        "name": "lifecycle_status",
                        "label": "Lifecycle status",
                        "field_type": "Char",
                        "required": True,
                        "index": True,
                    },
                ],
                "actions": [
                    {
                        "name": "transition",
                        "label": "Request transition",
                        "command_type": "incpros.entity.transition.requested.v1",
                        "payload_fields": ["target_status", "reason_code"],
                    }
                ],
            }
        ],
    }


def test_protocol_base_generates_shared_crosswalk_receipts_outbox_and_recovery() -> None:
    generated = render_thin_bridge_addon(_base_spec())
    files = generated.file_map()
    manifest = ast.literal_eval(files["__manifest__.py"].decode())
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    protocol = files["models/protocol.py"].decode()
    tests = files["tests/test_protocol.py"].decode()

    assert manifest["depends"] == ["base", "mail"]
    assert contract["models"] == [
        "fm.incpros.action.receipt",
        "fm.incpros.command.outbox",
        "fm.incpros.crosswalk",
        "fm.incpros.projection.receipt",
    ]
    assert contract["authority_fields"] == [
        "network_id",
        "business_id",
        "app_id",
        "channel_id",
        "identity_id",
        "actor_id",
        "company_id",
        "install_id",
    ]
    assert "class FmIncprosProjectionMixin(models.AbstractModel)" in protocol
    assert "_name = 'fm.incpros.crosswalk'" in protocol
    assert "_name = 'fm.incpros.projection.receipt'" in protocol
    assert "_name = 'fm.incpros.action.receipt'" in protocol
    assert "_name = 'fm.incpros.command.outbox'" in protocol
    assert "FOR UPDATE SKIP LOCKED" in protocol
    assert "pg_advisory_xact_lock" in protocol
    assert "def _deliver_command(self):" in protocol
    assert "test_duplicate_action_delivery_is_idempotent" in tests
    assert "test_action_fingerprint_conflict_fails_closed" in tests
    assert "test_timeout_recovery_fences_stale_worker" in tests
    assert "test_two_company_isolation" in tests


def test_projection_addon_depends_on_base_and_uses_shared_protocol_models() -> None:
    generated = render_thin_bridge_addon(_projection_spec())
    files = generated.file_map()
    manifest = ast.literal_eval(files["__manifest__.py"].decode())
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    projections = files["models/projections.py"].decode()
    tests = files["tests/test_projections.py"].decode()

    assert manifest["depends"] == ["base", "mail", "fm_incpros_base"]
    assert contract["models"] == ["fm.incpros.entity"]
    assert contract["receipt_model"] == "fm.incpros.action.receipt"
    assert contract["allowlisted_actions"] == {
        "transition": "incpros.entity.transition.requested.v1",
    }
    assert "_inherit = ['fm.incpros.projection.mixin', 'mail.thread', 'mail.activity.mixin']" in projections
    assert "def action_transition(" in projections
    assert "payload_fields=('target_status', 'reason_code')" in projections
    assert "test_projection_both_directions_and_loop_prevention" in tests
    assert "test_duplicate_projection_delivery" in tests
    assert "test_projection_fingerprint_conflict" in tests
    assert "test_projection_version_conflict" in tests


def test_standalone_addon_can_own_protocol_for_multiple_concepts() -> None:
    raw = {
        **_base_spec(),
        "module_name": "fm_hub_flex",
        "module_title": "FMOS Hub Flex",
        "protocol_model_prefix": "fm.hub.flex",
        "role": "standalone",
        "protocol_actions": [
            {
                "aggregate_model": "fm.hub.flex.profile",
                "name": "update_profile",
                "label": "Update profile",
                "command_type": "hub.flex.profile.update.requested.v1",
                "payload_fields": ["status"],
            }
        ],
        "concepts": [
            {
                "name": "profile",
                "title": "Flex profile",
                "model_name": "fm.hub.flex.profile",
                "fields": [
                    {
                        "name": "status",
                        "label": "Status",
                        "field_type": "Char",
                        "required": True,
                        "index": True,
                    }
                ],
                "actions": [
                    {
                        "name": "update_profile",
                        "label": "Update profile",
                        "command_type": "hub.flex.profile.update.requested.v1",
                        "payload_fields": ["status"],
                    }
                ],
            },
            {
                "name": "earning",
                "title": "Flex earning",
                "model_name": "fm.hub.flex.earning",
                "fields": [
                    {
                        "name": "gross_total_cents",
                        "label": "Gross total cents",
                        "field_type": "Integer",
                        "required": True,
                        "index": False,
                    }
                ],
                "actions": [],
            },
        ],
    }
    generated = render_thin_bridge_addon(raw)
    contract = json.loads(generated.file_map()["INSTALL_CONTRACT.json"])
    assert contract["models"] == sorted([
        "fm.hub.flex.profile",
        "fm.hub.flex.earning",
        "fm.hub.flex.crosswalk",
        "fm.hub.flex.projection.receipt",
        "fm.hub.flex.action.receipt",
        "fm.hub.flex.command.outbox",
    ])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec.update({"module_name": "../../escape"}), "module_name"),
        (lambda spec: spec.update({"base_module": "fm_wrong"}), "base_module"),
        (
            lambda spec: spec["concepts"][0]["fields"].append(
                {
                    "name": "customer_email",
                    "label": "Customer email",
                    "field_type": "Char",
                    "required": False,
                    "index": False,
                }
            ),
            "sensitive",
        ),
        (
            lambda spec: spec["concepts"][0]["actions"][0]["payload_fields"].append("operator_notes"),
            "sensitive",
        ),
    ],
)
def test_family_schema_fails_closed(mutation, message: str) -> None:
    raw = _projection_spec()
    mutation(raw)
    with pytest.raises(ValidationError, match=message):
        parse_thin_bridge_spec(raw)


def test_generated_python_xml_and_receipt_are_deterministic() -> None:
    first = render_thin_bridge_addon(_projection_spec())
    second = render_thin_bridge_addon(json.loads(json.dumps(_projection_spec())))
    assert first == second
    for path, content in first.file_map().items():
        if path.endswith(".py"):
            compile(content, path, "exec")
        elif path.endswith(".xml"):
            ET.fromstring(content)
    receipt = json.loads(first.file_map()[".amil-bridge.json"])
    assert receipt["module"] == "fm_incpros_entities"
    assert receipt["schema_version"] == "2.0"


def test_fmos806_specs_are_complete_reproducible_and_cross_family_consistent() -> None:
    specs = {
        path.stem: parse_thin_bridge_spec(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(FMOS806_SPECS.glob("*.json"))
    }
    assert set(specs) == {
        "fm_incpros_base",
        "fm_incpros_entities",
        "fm_incpros_intakes",
        "fm_incpros_matters",
        "fm_hub_flex",
    }
    base_actions = {
        (
            action.aggregate_model,
            action.name,
            action.command_type,
            action.payload_fields,
        )
        for action in specs["fm_incpros_base"].protocol_actions
    }
    child_actions = {
        (
            concept.model_name,
            action.name,
            action.command_type,
            action.payload_fields,
        )
        for module in ("fm_incpros_entities", "fm_incpros_intakes", "fm_incpros_matters")
        for concept in specs[module].concepts
        for action in concept.actions
    }
    assert base_actions == child_actions

    for module, spec in specs.items():
        first = render_thin_bridge_addon(spec)
        second = render_thin_bridge_addon(spec)
        assert first == second
        assert first.module_name == module
        contract = json.loads(first.file_map()["INSTALL_CONTRACT.json"])
        assert contract["action_semantics"]["data_policy"] == {
            "free_form_payloads": False,
            "operator_data": False,
            "pii": False,
            "secrets": False,
        }
        for path, content in first.file_map().items():
            if path.endswith(".py"):
                compile(content, path, "exec")
            elif path.endswith(".xml"):
                ET.fromstring(content)


def test_incpros_family_owns_exact_bounded_context_models_and_no_incgpt_copy() -> None:
    specs = {
        module: parse_thin_bridge_spec(
            json.loads((FMOS806_SPECS / f"{module}.json").read_text(encoding="utf-8"))
        )
        for module in INCPROS_MODULES
    }

    assert all(spec.version == "19.0.1.1.0" for spec in specs.values())
    assert all(spec.provider_contract_id == "fmos.incpros.customer.v1" for spec in specs.values())
    assert all(spec.wrapper_contract_id == "fmos.incpros.odoo-wrapper.v1" for spec in specs.values())

    for module, spec in specs.items():
        generated = render_thin_bridge_addon(spec)
        files = generated.file_map()
        contract = json.loads(files["INSTALL_CONTRACT.json"])
        receipt = json.loads(files[".amil-bridge.json"])
        manifest = ast.literal_eval(files["__manifest__.py"].decode())

        assert contract["models"] == sorted(EXPECTED_INCPROS_MODELS[module])
        assert set(contract["authority_fields"]) == {
            "network_id",
            "business_id",
            "app_id",
            "channel_id",
            "identity_id",
            "actor_id",
            "company_id",
            "install_id",
        }
        assert contract["authority_semantics"]["fallbacks"] is False
        assert contract["action_semantics"]["dynamic_model_or_method_dispatch"] is False
        assert contract["action_semantics"]["data_policy"] == {
            "free_form_payloads": False,
            "operator_data": False,
            "pii": False,
            "secrets": False,
        }
        expected_depends = ["base", "mail"]
        if module != "fm_incpros_base":
            expected_depends.append("fm_incpros_base")
        assert manifest["depends"] == expected_depends

        declared_hashes = {entry["path"]: entry["sha256"] for entry in receipt["files"]}
        assert declared_hashes == {
            path: hashlib.sha256(content).hexdigest()
            for path, content in files.items()
            if path != ".amil-bridge.json"
        }

        models_source = files.get("models/projections.py", b"").decode()
        views_source = files.get("views/projection_views.xml", b"").decode()
        access_source = files["security/ir.model.access.csv"].decode()
        rules_source = files["security/record_rules.xml"].decode()
        for model_name in EXPECTED_INCPROS_MODELS[module]:
            assert "incgpt" not in model_name
            if module == "fm_incpros_base":
                continue
            model_ref = f"model_{model_name.replace('.', '_')}"
            concept_class = "".join(part.title() for part in model_name.split(".")[2:])
            assert f"_name = {model_name!r}" in models_source
            assert f"<field name=\"model\">{model_name}</field>" in views_source
            assert model_ref in access_source
            assert model_ref in rules_source
            assert f"class Test{concept_class}Projection" in files["tests/test_projections.py"].decode()
