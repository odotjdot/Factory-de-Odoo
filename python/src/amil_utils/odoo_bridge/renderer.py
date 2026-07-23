"""Pure deterministic renderer and path-safe materializer for bridge addons."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from amil_utils.odoo_bridge.schema import BridgeSpec, parse_bridge_spec
from amil_utils.odoo_bridge.templates import render_files


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    """One immutable generated addon file."""

    path: PurePosixPath
    content: bytes

    @property
    def sha256(self) -> str:
        """Return the content digest used in the generation receipt."""
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class GeneratedAddon:
    """A complete, reproducible addon artifact set."""

    module_name: str
    spec_sha256: str
    files: tuple[GeneratedFile, ...]
    receipt: GeneratedFile

    def file_map(self) -> dict[str, bytes]:
        """Return a fresh relative-path mapping for inspection or packaging."""
        return {file.path.as_posix(): file.content for file in (*self.files, self.receipt)}


def _canonical_spec(spec: BridgeSpec) -> bytes:
    payload = spec.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _validate_generated_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError(f"generated path must be relative: {path}")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"generated path contains an unsafe segment: {path}")
    return candidate


def render_bridge_addon(spec: BridgeSpec | Mapping[str, Any]) -> GeneratedAddon:
    """Compile a strict bridge spec into a deterministic immutable artifact set."""
    validated = spec if isinstance(spec, BridgeSpec) else parse_bridge_spec(spec)
    canonical_spec = _canonical_spec(validated)
    spec_sha256 = hashlib.sha256(canonical_spec).hexdigest()

    rendered = render_files(validated)
    install_contract = json.loads(rendered["INSTALL_CONTRACT.json"])
    receipt_model = f"fm.{validated.domain}.action.receipt"
    outbox_model = f"fm.{validated.domain}.command.outbox"
    generated_models = (
        [validated.model_name, receipt_model, outbox_model]
        if validated.bridge_mode == "fmos_authoritative"
        else [receipt_model, outbox_model]
    )
    extended_models = [validated.model_name] if validated.bridge_mode == "odoo_native_extension" else []
    install_contract["models"] = [*generated_models, *extended_models]
    install_contract["extended_models"] = extended_models
    install_contract["provenance"] = {
        "kind": "factory-generated",
        "generator": "amil_utils.odoo_bridge",
        "spec_sha256": spec_sha256,
    }
    rendered["INSTALL_CONTRACT.json"] = json.dumps(install_contract, indent=2, sort_keys=True) + "\n"
    rendered["bridge-spec.json"] = canonical_spec.decode()
    base_files = tuple(
        GeneratedFile(_validate_generated_path(path), content.encode("utf-8"))
        for path, content in sorted(rendered.items())
    )
    receipt = {
        "generator": "amil_utils.odoo_bridge",
        "schema_version": validated.schema_version,
        "module": validated.module_name,
        "odoo_version": "19.0",
        "spec_sha256": spec_sha256,
        "files": [{"path": file.path.as_posix(), "sha256": file.sha256} for file in base_files],
    }
    receipt_content = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    receipt_file = GeneratedFile(PurePosixPath(".amil-bridge.json"), receipt_content)
    if len(base_files) != len({file.path for file in base_files}):
        raise ValueError("renderer produced duplicate output paths")
    return GeneratedAddon(validated.module_name, spec_sha256, base_files, receipt_file)


def _write_generated_file(module_dir: Path, generated: GeneratedFile) -> None:
    """Write one staged file durably below the already-private staging tree."""
    destination = module_dir.joinpath(*generated.path.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = destination.parent.resolve()
    if not resolved_parent.is_relative_to(module_dir.resolve()):
        raise ValueError(f"generated destination escapes module directory: {generated.path}")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(generated.content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(destination)


def _verify_managed_tree(module_dir: Path) -> None:
    """Reject unmanaged, missing, symlinked, or modified prior generated output."""
    receipt_path = module_dir / ".amil-bridge.json"
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise ValueError("refusing to replace an output without a regular generation receipt")
    prior = json.loads(receipt_path.read_text(encoding="utf-8"))
    entries = prior.get("files")
    if not isinstance(entries, list):
        raise TypeError("refusing to replace an output with an invalid generation receipt")
    prior_hashes: dict[str, str] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry["path"], str)
            or not isinstance(entry["sha256"], str)
        ):
            raise ValueError("refusing to replace an output with an invalid generation receipt")
        receipt_relative_path = _validate_generated_path(entry["path"]).as_posix()
        if receipt_relative_path in prior_hashes:
            raise ValueError("refusing to replace an output with duplicate receipt paths")
        prior_hashes[receipt_relative_path] = entry["sha256"]

    actual_paths: set[str] = set()
    for actual_path in module_dir.rglob("*"):
        if actual_path.is_symlink():
            raise ValueError("refusing to replace output containing symlinks")
        if actual_path.is_file():
            actual_paths.add(actual_path.relative_to(module_dir).as_posix())
    if actual_paths != set(prior_hashes) | {".amil-bridge.json"}:
        raise ValueError("refusing to replace output containing unmanaged or missing files")
    for relative_path, expected_hash in prior_hashes.items():
        actual_hash = hashlib.sha256((module_dir / relative_path).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("refusing to replace output containing modified generated files")


def _atomic_exchange(left: Path, right: Path) -> None:
    """Atomically exchange two directories on supported production platforms."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(os.fsencode(left), os.fsencode(right), 0x00000002)
    elif sys.platform.startswith("linux"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 0x00000002)
    else:
        raise OSError(f"atomic directory exchange is unsupported on {sys.platform}")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), f"{left} <-> {right}")


def materialize_bridge_addon(
    addon: GeneratedAddon,
    *,
    output_root: Path,
    replace_generated: bool = False,
) -> Path:
    """Atomically materialize an addon below one exact output root.

    Existing directories are accepted only when every existing file is declared
    by the prior generation receipt and ``replace_generated`` is true.
    """
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    module_dir = root / addon.module_name
    if module_dir.is_symlink():
        raise ValueError("output module directory cannot be a symlink")

    if module_dir.exists():
        if not replace_generated:
            raise FileExistsError(f"output module already exists: {module_dir}")
        if not module_dir.is_dir():
            raise ValueError("output module path must be a directory")
        _verify_managed_tree(module_dir)

    stage_dir = Path(tempfile.mkdtemp(prefix=f".{addon.module_name}.stage-", dir=root))
    try:
        for generated in addon.files:
            _write_generated_file(stage_dir, generated)
        _write_generated_file(stage_dir, addon.receipt)
        if module_dir.exists():
            _atomic_exchange(stage_dir, module_dir)
            shutil.rmtree(stage_dir)
        else:
            os.replace(stage_dir, module_dir)
    except BaseException:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        raise
    return module_dir
