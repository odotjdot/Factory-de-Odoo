"""Contract tests for read-only Odoo-native provider wrappers."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from amil_utils.odoo_bridge import (
    parse_native_readonly_spec,
    render_native_readonly_addon,
)

POS_SPEC = (
    Path(__file__).parents[1]
    / "specs"
    / "fmos806-native"
    / "fmos_headless_portal_pos.json"
)


def _raw_spec() -> dict:
    return json.loads(POS_SPEC.read_text(encoding="utf-8"))


def test_pos_wrapper_is_exact_read_only_native_registration() -> None:
    generated = render_native_readonly_addon(_raw_spec())
    files = generated.file_map()
    manifest = ast.literal_eval(files["__manifest__.py"].decode())
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    models = files["models/native_models.py"].decode()

    assert generated.module_name == "fmos_headless_portal_pos"
    assert manifest["depends"] == ["point_of_sale"]
    assert contract["bridge_mode"] == "odoo_native_extension"
    assert contract["authoritative_writer"] == "odoo"
    assert contract["aggregate_model"] == "pos.order"
    assert contract["models"] == [
        "pos.order",
        "pos.order.line",
        "pos.payment",
        "pos.payment.method",
    ]
    assert contract["contracts"] == {
        "provider": "odoo.pos-customer.v1",
        "wrapper": "fmos.headless-portal-pos.wrapper.v1",
        "projection": None,
        "action": None,
    }
    assert contract["allowlisted_actions"] == {}
    assert models.count("_inherit = ") == 4
    for prohibited in (
        "def create(",
        "def write(",
        "def unlink(",
        "def action_",
        "fields.",
        "canonical_id",
        "command",
        "outbox",
    ):
        assert prohibited not in models


def test_pos_wrapper_is_byte_deterministic_and_receipted() -> None:
    first = render_native_readonly_addon(_raw_spec())
    second = render_native_readonly_addon(json.loads(json.dumps(_raw_spec())))
    assert first == second
    receipt = json.loads(first.file_map()[".amil-bridge.json"])
    assert receipt["module"] == "fmos_headless_portal_pos"
    assert receipt["spec_sha256"] == first.spec_sha256
    assert {entry["path"] for entry in receipt["files"]} == (
        set(first.file_map()) - {".amil-bridge.json"}
    )
    for path, content in first.file_map().items():
        if path.endswith(".py"):
            compile(content, path, "exec")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec.update({"module_name": "../../escape"}), "module_name"),
        (lambda spec: spec["models"].append("pos.order"), "models must be unique"),
        (lambda spec: spec.update({"aggregate_model": "sale.order"}), "aggregate_model"),
        (lambda spec: spec["dependencies"].append("../point_of_sale"), "dependencies"),
        (lambda spec: spec.update({"unknown": True}), "Extra inputs are not permitted"),
    ],
)
def test_native_readonly_schema_fails_closed(mutation, message: str) -> None:
    raw = _raw_spec()
    mutation(raw)
    with pytest.raises(ValidationError, match=message):
        parse_native_readonly_spec(raw)
