"""Factory provenance contract for the canonical Odoo-owned Appointments domain."""

from __future__ import annotations

import json
from pathlib import Path

from amil_utils.odoo_bridge import render_existing_native_domain_addon

FACTORY_ROOT = Path(__file__).parents[2]
SPEC = FACTORY_ROOT / "python" / "specs" / "fmos808" / "fm_appointments.json"
ADDON = Path("/Users/odotjdot/APPS/fmservices/oca-custom/fm_appointments")
GENERATED = {".amil-bridge.json", "INSTALL_CONTRACT.json", "bridge-spec.json"}


def _source() -> dict[str, bytes]:
    return {
        path.relative_to(ADDON).as_posix(): path.read_bytes()
        for path in sorted(ADDON.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(ADDON).as_posix() not in GENERATED
    }


def test_appointments_existing_domain_and_bff_are_byte_preserved_and_receipted() -> None:
    raw = json.loads(SPEC.read_text(encoding="utf-8"))
    source = _source()
    generated = render_existing_native_domain_addon(raw, source)
    files = generated.file_map()
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    bridge = json.loads(files["bridge-spec.json"])
    receipt = json.loads(files[".amil-bridge.json"])

    for path, content in source.items():
        assert files[path] == content
    assert contract["module"] == "fm_appointments"
    assert contract["authoritative_writer"] == "odoo"
    assert contract["bridge_mode"] == "odoo_native_extension"
    assert contract["contracts"] == {
        "action": None,
        "projection": None,
        "provider": "fmos.appointments.customer.v1",
        "wrapper": "fmos.appointments.odoo-wrapper.v1",
    }
    assert contract["aggregate_model"] == "fm.appointment.booking"
    assert contract["extended_models"] == ["fm.appointment.customer.service"]
    assert bridge["service_model"] == "fm.appointment.customer.service"
    assert "validate_link" in bridge["service_methods"]
    assert "complete_booking" in bridge["service_methods"]
    assert {entry["path"] for entry in receipt["files"]} == set(files) - {".amil-bridge.json"}
