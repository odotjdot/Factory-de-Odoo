"""Renderer for deterministic read-only Odoo-native provider wrappers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from amil_utils.odoo_bridge.native_readonly_schema import (
    NativeExistingReadOnlySpec,
    NativeReadOnlySpec,
    parse_existing_native_readonly_spec,
    parse_native_readonly_spec,
)
from amil_utils.odoo_bridge.native_readonly_templates import render_native_readonly_files
from amil_utils.odoo_bridge.renderer import GeneratedAddon, GeneratedFile


def _canonical_spec(spec: NativeReadOnlySpec) -> bytes:
    return (
        json.dumps(
            spec.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _install_contract(spec: NativeReadOnlySpec, spec_sha256: str) -> str:
    contract = {
        "schema_version": "1.0",
        "odoo_version": "19.0",
        "module": spec.module_name,
        "bridge_mode": "odoo_native_extension",
        "authoritative_writer": "odoo",
        "aggregate_model": spec.aggregate_model,
        "models": list(spec.models),
        "contracts": {
            "provider": spec.provider_contract_id,
            "wrapper": spec.wrapper_contract_id,
            "projection": None,
            "action": None,
        },
        "allowlisted_actions": {},
        "dependencies": list(spec.dependencies),
        "extended_models": list(spec.extended_models),
        "provenance": {
            "kind": "factory-generated",
            "generator": "amil_utils.odoo_bridge",
            "spec_sha256": spec_sha256,
        },
    }
    return json.dumps(contract, indent=2, sort_keys=True) + "\n"


def _receipt(
    spec: NativeReadOnlySpec,
    spec_sha256: str,
    files: tuple[GeneratedFile, ...],
) -> GeneratedFile:
    payload = {
        "generator": "amil_utils.odoo_bridge",
        "schema_version": spec.schema_version,
        "module": spec.module_name,
        "odoo_version": "19.0",
        "spec_sha256": spec_sha256,
        "files": [
            {"path": generated.path.as_posix(), "sha256": generated.sha256}
            for generated in files
        ],
    }
    return GeneratedFile(
        PurePosixPath(".amil-bridge.json"),
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
    )


def render_native_readonly_addon(
    spec: NativeReadOnlySpec | Mapping[str, Any],
) -> GeneratedAddon:
    """Compile one read-only Odoo-native declaration into an immutable addon."""
    validated = (
        spec
        if isinstance(spec, NativeReadOnlySpec)
        else parse_native_readonly_spec(dict(spec))
    )
    canonical_spec = _canonical_spec(validated)
    spec_sha256 = hashlib.sha256(canonical_spec).hexdigest()
    rendered = render_native_readonly_files(validated)
    rendered["INSTALL_CONTRACT.json"] = _install_contract(validated, spec_sha256)
    rendered["bridge-spec.json"] = canonical_spec.decode()
    files = tuple(
        GeneratedFile(PurePosixPath(path), content.encode())
        for path, content in sorted(rendered.items())
    )
    receipt_file = _receipt(validated, spec_sha256, files)
    return GeneratedAddon(
        module_name=validated.module_name,
        spec_sha256=spec_sha256,
        files=files,
        receipt=receipt_file,
    )


def render_existing_native_readonly_addon(
    spec: NativeExistingReadOnlySpec | Mapping[str, Any],
    preserved_files: Mapping[str, bytes],
) -> GeneratedAddon:
    """Seal existing addon behavior while adding a generated read-only contract."""
    validated = (
        spec
        if isinstance(spec, NativeExistingReadOnlySpec)
        else parse_existing_native_readonly_spec(dict(spec))
    )
    expected = {item.path: item.sha256 for item in validated.preserved_files}
    if set(preserved_files) != set(expected):
        raise ValueError("preserved source files do not exactly match the seal declaration")
    preserved = tuple(
        GeneratedFile(PurePosixPath(path), bytes(content))
        for path, content in sorted(preserved_files.items())
    )
    for generated in preserved:
        if generated.sha256 != expected[generated.path.as_posix()]:
            raise ValueError(
                f"preserved source digest mismatch: {generated.path.as_posix()}"
            )
    canonical_spec = _canonical_spec(validated)
    spec_sha256 = hashlib.sha256(canonical_spec).hexdigest()
    runtime_tests = (
        '"""Runtime installability tests for the sealed native provider."""\n\n'
        "from odoo.tests import tagged\n"
        "from odoo.tests.common import TransactionCase\n\n\n"
        '@tagged("post_install", "-at_install")\n'
        f"class Test{''.join(part.capitalize() for part in validated.module_name.split('_'))}"
        "(TransactionCase):\n"
        "    def test_exact_native_models_are_registered(self):\n"
        f"        expected = {(validated.models + validated.extended_models)!r}\n"
        "        self.assertEqual(expected, tuple(self.env[model]._name for model in expected))\n\n"
        "    def test_existing_provider_remains_installed(self):\n"
        '        module = self.env["ir.module.module"].search(\n'
        f'            [("name", "=", {validated.module_name!r})], limit=1,\n'
        "        )\n"
        '        self.assertEqual(module.state, "installed")\n'
    )
    generated_overlay = [
        GeneratedFile(
            PurePosixPath("INSTALL_CONTRACT.json"),
            _install_contract(validated, spec_sha256).encode(),
        ),
        GeneratedFile(PurePosixPath("bridge-spec.json"), canonical_spec),
    ]
    if validated.generate_contract_tests:
        generated_overlay.extend(
            (
                GeneratedFile(
                    PurePosixPath("tests/__init__.py"),
                    b"from . import test_native_readonly\n",
                ),
                GeneratedFile(
                    PurePosixPath("tests/test_native_readonly.py"),
                    runtime_tests.encode(),
                ),
            )
        )
    files = tuple(sorted((*preserved, *generated_overlay), key=lambda item: item.path))
    return GeneratedAddon(
        module_name=validated.module_name,
        spec_sha256=spec_sha256,
        files=files,
        receipt=_receipt(validated, spec_sha256, files),
    )


render_existing_native_domain_addon = render_existing_native_readonly_addon
