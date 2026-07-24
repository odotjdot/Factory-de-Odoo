#!/usr/bin/env python3
"""Seal one existing Odoo-owned addon with deterministic Factory provenance."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from amil_utils.odoo_bridge import render_existing_native_domain_addon

GENERATED = frozenset({".amil-bridge.json", "INSTALL_CONTRACT.json", "bridge-spec.json"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--addon", type=Path, required=True)
    return parser.parse_args()


def source_files(addon: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(addon.rglob("*")):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(addon).as_posix()
        if relative not in GENERATED:
            files[relative] = path.read_bytes()
    return files


def atomic_write(destination: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(destination)


def main() -> int:
    args = parse_args()
    addon = args.addon.resolve()
    raw = json.loads(args.spec.resolve().read_text(encoding="utf-8"))
    if addon.name != raw.get("module_name"):
        raise ValueError("addon directory does not match the Factory specification")
    generated = render_existing_native_domain_addon(raw, source_files(addon))
    files = generated.file_map()
    for relative in sorted(GENERATED):
        atomic_write(addon / relative, files[relative])
    print(json.dumps({
        "module": generated.module_name,
        "spec_sha256": generated.spec_sha256,
        "sealed_files": sorted(GENERATED),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
