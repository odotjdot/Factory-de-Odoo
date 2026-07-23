"""Contract tests for the deterministic Odoo 19 FMOS bridge compiler."""

from __future__ import annotations

import ast
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pydantic import ValidationError

from amil_utils.odoo_bridge import (
    load_bridge_spec,
    materialize_bridge_addon,
    parse_bridge_spec,
    render_bridge_addon,
)
from amil_utils.odoo_bridge import renderer as bridge_renderer

FIXTURES = Path(__file__).parent / "fixtures"
FAM_SPEC_PATH = FIXTURES / "fam_bridge_spec.json"
FAM_REFERENCE_ADDON = FIXTURES / "generated_fam_bridge" / "fm_fam_bridge"

EXPECTED_FILES = {
    ".amil-bridge.json",
    "INSTALL_CONTRACT.json",
    "README.rst",
    "__init__.py",
    "__manifest__.py",
    "bridge-spec.json",
    "data/cron.xml",
    "hooks.py",
    "i18n/fm_fam_bridge.pot",
    "migrations/19.0.1.0.0/post-migration.py",
    "migrations/19.0.1.0.0/pre-migration.py",
    "models/__init__.py",
    "models/action_receipt.py",
    "models/command_outbox.py",
    "models/fam_bridge.py",
    "security/groups.xml",
    "security/ir.model.access.csv",
    "security/record_rules.xml",
    "tests/__init__.py",
    "tests/test_bridge.py",
    "views/action_receipt_views.xml",
    "views/bridge_record_views.xml",
    "views/command_outbox_views.xml",
    "views/menu.xml",
}


@pytest.fixture
def raw_spec() -> dict:
    """Return a fresh FAM reference input."""
    return json.loads(FAM_SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def generated(raw_spec: dict):
    """Compile the FAM reference input."""
    return render_bridge_addon(raw_spec)


def test_loads_strict_reference_spec() -> None:
    spec = load_bridge_spec(Path("fam_bridge_spec.json"), allowed_root=FIXTURES)
    assert spec.model_name == "fm.fam.asset"
    assert spec.bridge_mode == "fmos_authoritative"
    assert spec.actions[0].command_type == "fam.download.requested.v1"
    assert spec.provider_contract_id == "fmos.fam.customer-library.v1"
    assert spec.wrapper_contract_id == "fam.wrapper.v1"
    assert spec.projection_contract_id == "fam.projection.v1"
    assert spec.action_contract_id == "fam.action.v1"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec.update({"unknown": True}), "Extra inputs are not permitted"),
        (lambda spec: spec.update({"module_name": "../../escape"}), "module_name"),
        (lambda spec: spec.update({"model_name": "res.partner"}), "FMOS-authoritative"),
        (lambda spec: spec.update({"version": "18.0.1.0.0"}), "Odoo 19"),
        (lambda spec: spec["fields"].append(spec["fields"][0]), "field names must be unique"),
        (lambda spec: spec["actions"].append(spec["actions"][0]), "action names must be unique"),
        (
            lambda spec: spec["actions"][0].update({"command_type": "runtime.method"}),
            "versioned identifier",
        ),
        (
            lambda spec: spec.update({"retry_cap_seconds": 30}),
            "greater than or equal",
        ),
        (
            lambda spec: spec.update({"projection_contract_id": "ProjectionV1"}),
            "contract IDs",
        ),
        (
            lambda spec: spec.update({"action_contract_id": spec["wrapper_contract_id"]}),
            "must be distinct",
        ),
    ],
)
def test_spec_rejects_unsafe_or_ambiguous_input(raw_spec: dict, mutation, message: str) -> None:
    mutation(raw_spec)
    with pytest.raises(ValidationError, match=message):
        parse_bridge_spec(raw_spec)


def test_native_extension_is_allowlisted_and_uses_odoo_as_sole_writer(raw_spec: dict) -> None:
    raw_spec.update(
        {
            "bridge_mode": "odoo_native_extension",
            "model_name": "project.project",
        }
    )
    generated = render_bridge_addon(raw_spec)
    files = generated.file_map()
    aggregate = files["models/fam_bridge.py"].decode()
    receipt = files["models/action_receipt.py"].decode()
    outbox = files["models/command_outbox.py"].decode()
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    manifest = ast.literal_eval(files["__manifest__.py"].decode())

    assert parse_bridge_spec(raw_spec).model_name == "project.project"
    assert manifest["depends"] == ["base", "project"]
    assert "_inherit = 'project.project'" in aggregate
    assert "_name = 'project.project'" not in aggregate
    assert "apply_fmos_projection" not in aggregate
    assert "def create(" not in aggregate
    assert "def write(" not in aggregate
    assert "network_id = fields." not in aggregate
    assert "canonical_id = fields." not in aggregate
    assert "def action_request_download(" in aggregate
    assert "def _capture_native_action(" in receipt
    assert "source_snapshot" in receipt
    assert "source_snapshot" in outbox
    for authority_field in (
        "network_id",
        "business_id",
        "app_id",
        "channel_id",
        "identity_id",
        "actor_id",
        "company_id",
        "install_id",
    ):
        assert f"{authority_field} = fields." in receipt
        assert f"{authority_field} = fields." in outbox
    assert contract["authoritative_writer"] == "odoo"
    assert contract["directions"]["fmos_to_odoo"] == "prohibited"
    assert contract["projection_semantics"]["supported"] is False
    assert contract["models"] == [
        "fm.fam.action.receipt",
        "fm.fam.command.outbox",
        "project.project",
    ]
    assert contract["extended_models"] == ["project.project"]
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
    assert contract["authority_semantics"]["required"] is True
    assert contract["retry_dead_letter_recovery"]["lease_fencing"].startswith("unguessable processing token")
    assert "test_ordinary_native_create_remains_valid" in files["tests/test_bridge.py"].decode()
    assert "test_native_extension_has_no_fmos_projection_path" in files["tests/test_bridge.py"].decode()
    assert "project.group_project_manager" in files["tests/test_bridge.py"].decode()
    for path, content in files.items():
        if path.endswith(".py"):
            compile(content, path, "exec")
        elif path.endswith(".xml"):
            ET.fromstring(content)

    raw_spec["model_name"] = "runtime.selected.model"
    with pytest.raises(ValidationError, match="not allowlisted"):
        parse_bridge_spec(raw_spec)


def test_spec_loader_rejects_absolute_traversal_non_json_and_symlinks(tmp_path: Path) -> None:
    allowed = tmp_path / "specs"
    allowed.mkdir()
    safe = allowed / "safe.json"
    safe.write_text(FAM_SPEC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text(FAM_SPEC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (allowed / "linked.json").symlink_to(outside)
    (allowed / "not-json.txt").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="relative"):
        load_bridge_spec(safe, allowed_root=allowed)
    with pytest.raises(ValueError, match="unsafe path segment"):
        load_bridge_spec(Path("../outside.json"), allowed_root=allowed)
    with pytest.raises(ValueError, match="symlinks"):
        load_bridge_spec(Path("linked.json"), allowed_root=allowed)
    with pytest.raises(ValueError, match="extension"):
        load_bridge_spec(Path("not-json.txt"), allowed_root=allowed)


def test_duplicate_json_keys_fail_closed(tmp_path: Path) -> None:
    spec = tmp_path / "duplicate.json"
    spec.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_bridge_spec(Path("duplicate.json"), allowed_root=tmp_path)


def test_output_is_byte_for_byte_deterministic(raw_spec: dict) -> None:
    first = render_bridge_addon(raw_spec)
    second = render_bridge_addon(json.loads(json.dumps(raw_spec, sort_keys=True)))
    assert first == second
    assert first.spec_sha256 == second.spec_sha256
    assert first.file_map() == second.file_map()


def test_checked_fam_reference_addon_is_byte_exact(generated) -> None:
    checked = {
        path.relative_to(FAM_REFERENCE_ADDON).as_posix(): path.read_bytes()
        for path in FAM_REFERENCE_ADDON.rglob("*")
        if path.is_file()
    }
    assert checked == generated.file_map()


def test_exact_generated_file_set(generated) -> None:
    assert set(generated.file_map()) == EXPECTED_FILES
    receipt = json.loads(generated.file_map()[".amil-bridge.json"])
    assert receipt["module"] == "fm_fam_bridge"
    assert receipt["spec_sha256"] == generated.spec_sha256
    expected_payload_files = EXPECTED_FILES - {".amil-bridge.json"}
    assert {entry["path"] for entry in receipt["files"]} == expected_payload_files
    assert len(receipt["files"]) == len(expected_payload_files)
    for entry in receipt["files"]:
        assert entry["sha256"] == hashlib.sha256(generated.file_map()[entry["path"]]).hexdigest()


def test_manifest_load_order_dependencies_and_install_contract(generated) -> None:
    files = generated.file_map()
    manifest = ast.literal_eval(files["__manifest__.py"].decode())
    assert manifest["version"] == "19.0.1.0.0"
    assert manifest["depends"] == ["base"]
    assert manifest["post_init_hook"] == "post_init_hook"
    assert manifest["data"] == [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/cron.xml",
        "views/bridge_record_views.xml",
        "views/action_receipt_views.xml",
        "views/command_outbox_views.xml",
        "views/menu.xml",
    ]
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    assert contract["authoritative_writer"] == "fmos"
    assert contract["aggregate_model"] == "fm.fam.asset"
    assert contract["contracts"] == {
        "provider": "fmos.fam.customer-library.v1",
        "wrapper": "fam.wrapper.v1",
        "projection": "fam.projection.v1",
        "action": "fam.action.v1",
    }
    assert contract["models"] == [
        "fm.fam.asset",
        "fm.fam.action.receipt",
        "fm.fam.command.outbox",
    ]
    assert contract["extended_models"] == []
    assert contract["provenance"] == {
        "kind": "factory-generated",
        "generator": "amil_utils.odoo_bridge",
        "spec_sha256": generated.spec_sha256,
    }
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
    assert contract["authority_semantics"]["fallbacks"] is False
    assert contract["authority_semantics"]["aggregate_identity"] == "all eight authority fields plus canonical_id"
    assert contract["authority_semantics"]["cross_scope_reassignment"] == "prohibited"
    assert contract["action_semantics"]["expected_version"].startswith("required")
    assert "aggregate_record_id" in contract["action_semantics"]["semantic_fingerprint_material"]
    assert contract["telemetry_pii"] is False
    assert "reconciliation_state" in contract["reconciliation"]["persisted_fields"]
    assert contract["allowlisted_actions"]["request_share"] == "fam.share.requested.v1"
    assert contract["delivery_adapter"] == {
        "guarantee": "the generated addon cannot deliver until an explicit adapter is installed",
        "runtime_dispatch": False,
        "seam": "deployment addon overrides the fixed _deliver_command method",
        "status": "unbound",
    }


def test_company_isolation_acl_and_record_rules(generated) -> None:
    files = generated.file_map()
    model_source = files["models/fam_bridge.py"].decode()
    groups = files["security/groups.xml"].decode()
    rules = files["security/record_rules.xml"].decode()
    access = files["security/ir.model.access.csv"].decode()
    assert 'model="res.groups.privilege"' in groups
    assert 'name="privilege_id"' in groups
    assert 'model="res.groups"' in groups
    assert 'name="category_id"' not in groups.split('model="res.groups"', 1)[1]
    assert "_check_company_auto = True" in model_source
    assert "company_id = fields.Many2one(" in model_source
    assert "company_id not in self.env.companies.ids" in model_source
    for field_name in (
        "network_id",
        "business_id",
        "app_id",
        "channel_id",
        "identity_id",
        "actor_id",
        "install_id",
    ):
        assert f"{field_name} = fields.Char(" in model_source
    assert rules.count("[('company_id', 'in', company_ids)]") == 3
    assert rules.count('<field name="global" eval="True"/>') == 3
    assert ",,1," not in access
    assert "group_bridge_operator" in access
    assert "group_bridge_admin" in access
    ledger_rows = [
        row
        for row in access.splitlines()
        if "bridge_record" in row or "action_receipt" in row or "command_outbox" in row
    ]
    assert ledger_rows
    assert all(row.endswith(",1,0,0,0") for row in ledger_rows)


def test_projection_idempotency_fingerprint_and_version_conflict_are_generated(generated) -> None:
    source = generated.file_map()["models/fam_bridge.py"].decode()
    assert 'json.dumps(body, sort_keys=True, separators=(",", ":")' in source
    assert "fingerprint != expected_fingerprint" in source
    assert "canonical_version < record.canonical_version" in source
    assert "canonical_version == record.canonical_version" in source
    assert "fingerprint != record.fingerprint" in source
    assert "return record" in source
    for field_name in (
        "company_id",
        "network_id",
        "business_id",
        "app_id",
        "channel_id",
        "identity_id",
        "actor_id",
        "install_id",
        "canonical_id",
    ):
        assert field_name in source.split("_canonical_identity = models.UniqueIndex(", 1)[1].split(")", 2)[0]
    assert "SELECT pg_advisory_xact_lock(%s, %s)" in source
    assert '"authority_scope": authority_scope' in source
    assert "def _authority_domain(self, authority_scope):" in source
    assert 'self._authority_domain(authority) + [("canonical_id", "=", canonical_id)]' in source
    assert source.index("_acquire_projection_lock(authority") < source.index("record = scoped.search(")
    assert "cross-scope reassignment is prohibited" in source
    assert '"authority_scope": authority_scope' in source


def test_allowlist_has_fixed_methods_and_no_runtime_dispatch_surface(generated) -> None:
    files = generated.file_map()
    aggregate = files["models/fam_bridge.py"].decode()
    receipt = files["models/action_receipt.py"].decode()
    contract = files["INSTALL_CONTRACT.json"].decode()
    assert "def action_request_download(" in aggregate
    assert "def action_request_share(" in aggregate
    assert "def action_archive(" in aggregate
    assert "if action not in _ALLOWED_ACTIONS:" in aggregate
    assert "if action not in _ALLOWED_ACTIONS:" in receipt
    assert "expected_canonical_version != self.canonical_version" in aggregate
    assert "runtime_model" not in aggregate
    assert "runtime_method" not in aggregate
    assert "endpoint_url" not in aggregate
    assert '"runtime_dispatch": false' in contract


def test_atomic_receipt_outbox_retry_dead_letter_and_recovery_are_generated(generated) -> None:
    files = generated.file_map()
    receipt = files["models/action_receipt.py"].decode()
    outbox = files["models/command_outbox.py"].decode()
    assert "receipt = self.sudo().create(" in receipt
    assert "].sudo()._enqueue_receipt(receipt)" in receipt
    assert '_idempotency_identity = models.UniqueIndex("(company_id, idempotency_key)")' in receipt
    assert "def _acquire_idempotency_lock(self, company_id, idempotency_key):" in receipt
    assert receipt.index("_acquire_idempotency_lock(aggregate.company_id.id") < receipt.index(
        "existing = self.sudo().search("
    )
    for material in (
        "aggregate_record_id",
        "aggregate_canonical_id",
        "expected_canonical_version",
        "aggregate_fingerprint",
        "authority_scope",
        "action_contract_id",
        "semantic_fingerprint",
    ):
        assert material in receipt
        assert material in outbox or material == "authority_scope"
    assert "with self.env.cr.savepoint():" in outbox
    assert "FOR UPDATE SKIP LOCKED" in outbox
    assert "def _claim_due_commands(self):" in outbox
    assert "processing_lease_expires_at <= %s" in outbox
    assert "state = 'processing'" in outbox
    assert "next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)" in outbox
    assert "next_attempt_at = fields.Datetime(required=True" not in outbox
    assert '"next_attempt_at": False' in outbox
    assert '"processing_lease_expires_at": False' in outbox
    assert "2 ** max(attempt - 1, 0)" in outbox
    assert '"state": "dead_letter"' in outbox
    assert "def action_recover(self):" in outbox
    assert '"state": "retry"' in outbox
    assert "last_error_code" in outbox
    assert "dead_lettered_at" in outbox
    assert "dispatch_lag_seconds" in outbox
    assert "def action_request_reconciliation(self):" in files["models/fam_bridge.py"].decode()
    assert "def apply_reconciliation_observation(" in files["models/fam_bridge.py"].decode()
    assert "reconciliation_state" in files["models/fam_bridge.py"].decode()
    assert 'self.env["ir.cron"]._commit_progress' in outbox


def test_generated_python_compiles_and_xml_parses(generated) -> None:
    for path, content in generated.file_map().items():
        if path.endswith(".py"):
            compile(content, path, "exec")
        elif path.endswith(".xml"):
            ET.fromstring(content)


def test_generated_odoo_tests_cover_runtime_contract(generated) -> None:
    source = generated.file_map()["tests/test_bridge.py"].decode()
    assert "TransactionCase" in source
    assert "test_projection_is_idempotent_and_monotonic" in source
    assert "test_projection_takes_transaction_advisory_lock" in source
    assert "test_action_allowlist_and_atomic_outbox" in source
    assert "test_full_authority_scope_isolated_and_immutable" in source
    assert "test_reconciliation_observation_is_persisted" in source
    assert "test_bridge_ledgers_are_read_only_over_rpc" in source
    assert "test_retry_dead_letter_and_recovery" in source
    assert '@tagged("standard", "at_install")' in source


def test_materializer_is_path_safe_and_refuses_unmanaged_overwrite(generated, tmp_path: Path) -> None:
    module_dir = materialize_bridge_addon(generated, output_root=tmp_path)
    assert module_dir == tmp_path / "fm_fam_bridge"
    assert {
        path.relative_to(module_dir).as_posix() for path in module_dir.rglob("*") if path.is_file()
    } == EXPECTED_FILES

    with pytest.raises(FileExistsError):
        materialize_bridge_addon(generated, output_root=tmp_path)

    materialize_bridge_addon(generated, output_root=tmp_path, replace_generated=True)
    unmanaged = module_dir / "manual.txt"
    unmanaged.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ValueError, match="unmanaged"):
        materialize_bridge_addon(generated, output_root=tmp_path, replace_generated=True)


def test_materializer_rejects_symlinked_module_directory(generated, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / generated.module_name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        materialize_bridge_addon(generated, output_root=tmp_path)


def test_materializer_rejects_modified_generated_file(generated, tmp_path: Path) -> None:
    module_dir = materialize_bridge_addon(generated, output_root=tmp_path)
    (module_dir / "README.rst").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="modified generated files"):
        materialize_bridge_addon(generated, output_root=tmp_path, replace_generated=True)


def test_materializer_stages_complete_tree_receipt_last_and_rolls_back(
    generated,
    raw_spec: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []
    original_write = bridge_renderer._write_generated_file

    def recording_write(module_dir: Path, generated_file) -> None:
        writes.append(generated_file.path.as_posix())
        original_write(module_dir, generated_file)

    monkeypatch.setattr(bridge_renderer, "_write_generated_file", recording_write)
    module_dir = materialize_bridge_addon(generated, output_root=tmp_path)
    assert writes[-1] == ".amil-bridge.json"
    original_tree = {
        path.relative_to(module_dir).as_posix(): path.read_bytes() for path in module_dir.rglob("*") if path.is_file()
    }

    raw_spec["module_title"] = "Replacement FMOS FAM Bridge"
    replacement = render_bridge_addon(raw_spec)
    original_exchange = bridge_renderer._atomic_exchange

    def fail_exchange(left: Path, right: Path) -> None:
        raise OSError("injected atomic swap failure")

    monkeypatch.setattr(bridge_renderer, "_atomic_exchange", fail_exchange)
    with pytest.raises(OSError, match="injected atomic swap failure"):
        materialize_bridge_addon(replacement, output_root=tmp_path, replace_generated=True)
    after_failure = {
        path.relative_to(module_dir).as_posix(): path.read_bytes() for path in module_dir.rglob("*") if path.is_file()
    }
    assert after_failure == original_tree
    assert not list(tmp_path.glob(".fm_fam_bridge.stage-*"))

    monkeypatch.setattr(bridge_renderer, "_atomic_exchange", original_exchange)
    materialize_bridge_addon(replacement, output_root=tmp_path, replace_generated=True)
    replaced_tree = {
        path.relative_to(module_dir).as_posix(): path.read_bytes() for path in module_dir.rglob("*") if path.is_file()
    }
    assert replaced_tree == replacement.file_map()
    assert not list(tmp_path.glob(".fm_fam_bridge.stage-*"))
