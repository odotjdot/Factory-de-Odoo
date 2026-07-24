"""Tests for content-addressed sealing of an existing native provider addon."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from amil_utils.odoo_bridge import render_existing_native_readonly_addon

LOYALTY_SPEC = (
    Path(__file__).parents[1]
    / "specs"
    / "fmos806-native"
    / "fmos_headless_portal_loyalty.json"
)


def _spec_and_files() -> tuple[dict, dict[str, bytes]]:
    source = {
        "__init__.py": b"from . import models\n",
        "__manifest__.py": b"{'depends': ['loyalty']}\n",
        "models/__init__.py": b"from . import loyalty_registry\n",
        "models/loyalty_registry.py": b"EXISTING_BEHAVIOR = True\n",
    }
    raw = json.loads(LOYALTY_SPEC.read_text(encoding="utf-8"))
    raw["preserved_files"] = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in source.items()
    ]
    return raw, source


def test_existing_loyalty_behavior_is_byte_preserved_and_receipted() -> None:
    raw, source = _spec_and_files()
    generated = render_existing_native_readonly_addon(raw, source)
    files = generated.file_map()
    contract = json.loads(files["INSTALL_CONTRACT.json"])
    receipt = json.loads(files[".amil-bridge.json"])

    for path, content in source.items():
        assert files[path] == content
    assert contract["module"] == "fmos_headless_portal_loyalty"
    assert contract["authoritative_writer"] == "odoo"
    assert contract["contracts"]["provider"] == "odoo.loyalty-customer.v1"
    assert contract["contracts"]["projection"] is None
    assert contract["contracts"]["action"] is None
    assert contract["allowlisted_actions"] == {}
    assert contract["models"] == [
        "loyalty.card",
        "loyalty.history",
        "loyalty.program",
        "loyalty.reward",
    ]
    assert {entry["path"] for entry in receipt["files"]} == (
        set(files) - {".amil-bridge.json"}
    )
    assert "test_exact_native_models_are_registered" in files[
        "tests/test_native_readonly.py"
    ].decode()


def test_existing_seal_rejects_missing_extra_or_changed_source() -> None:
    raw, source = _spec_and_files()
    missing = dict(source)
    missing.pop("__init__.py")
    with pytest.raises(ValueError, match="exactly match"):
        render_existing_native_readonly_addon(raw, missing)

    extra = {**source, "models/extra.py": b""}
    with pytest.raises(ValueError, match="exactly match"):
        render_existing_native_readonly_addon(raw, extra)

    changed = {**source, "__init__.py": b"changed\n"}
    with pytest.raises(ValueError, match="digest mismatch"):
        render_existing_native_readonly_addon(raw, changed)
