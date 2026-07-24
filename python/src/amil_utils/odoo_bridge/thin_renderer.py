"""Renderer for schema-v2 modular FMOS thin Odoo addons."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from amil_utils.odoo_bridge.renderer import GeneratedAddon, GeneratedFile
from amil_utils.odoo_bridge.thin_schema import ThinBridgeSpec, parse_thin_bridge_spec
from amil_utils.odoo_bridge.thin_templates import render_thin_files


def _generated_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError(f"generated path must be relative: {path}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"generated path contains an unsafe segment: {path}")
    return candidate


def render_thin_bridge_addon(
    spec: ThinBridgeSpec | Mapping[str, Any],
) -> GeneratedAddon:
    """Compile one schema-v2 declaration into a deterministic addon."""
    validated = spec if isinstance(spec, ThinBridgeSpec) else parse_thin_bridge_spec(dict(spec))
    canonical_spec = (
        json.dumps(
            validated.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    spec_sha256 = hashlib.sha256(canonical_spec).hexdigest()
    rendered = render_thin_files(validated)
    contract = json.loads(rendered["INSTALL_CONTRACT.json"])
    contract["provenance"] = {
        "kind": "factory-generated",
        "generator": "amil_utils.odoo_bridge",
        "spec_sha256": spec_sha256,
    }
    rendered["INSTALL_CONTRACT.json"] = json.dumps(contract, indent=2, sort_keys=True) + "\n"
    rendered["bridge-spec.json"] = canonical_spec.decode()
    files = tuple(
        GeneratedFile(_generated_path(path), content.encode())
        for path, content in sorted(rendered.items())
    )
    receipt = {
        "generator": "amil_utils.odoo_bridge",
        "schema_version": validated.schema_version,
        "module": validated.module_name,
        "odoo_version": "19.0",
        "spec_sha256": spec_sha256,
        "files": [
            {"path": generated.path.as_posix(), "sha256": generated.sha256}
            for generated in files
        ],
    }
    receipt_file = GeneratedFile(
        PurePosixPath(".amil-bridge.json"),
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
    )
    return GeneratedAddon(
        module_name=validated.module_name,
        spec_sha256=spec_sha256,
        files=files,
        receipt=receipt_file,
    )
