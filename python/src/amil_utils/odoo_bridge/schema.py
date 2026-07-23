"""Strict declarative schema and path-safe loading for Odoo bridge addons."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MODULE_RE = re.compile(r"fm_[a-z][a-z0-9_]*_bridge\Z")
_MODEL_RE = re.compile(r"fm\.[a-z][a-z0-9_.]*\Z")
_FIELD_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_ACTION_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_COMMAND_RE = re.compile(r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*\Z")
_VERSION_RE = re.compile(r"19\.0\.[1-9][0-9]*\.[0-9]+\.[0-9]+\Z")

_RESERVED_FIELDS = frozenset(
    {
        "active",
        "canonical_id",
        "canonical_version",
        "causation_id",
        "company_id",
        "correlation_id",
        "fingerprint",
        "id",
        "last_projection_at",
        "origin",
    }
)

ODOO_NATIVE_TARGETS: Mapping[str, str] = {
    "account.move": "account",
    "event.registration": "event",
    "helpdesk.ticket": "helpdesk",
    "project.project": "project",
    "project.task": "project",
    "sale.order": "sale",
}


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class BridgeFieldSpec(BaseModel):
    """One canonical FMOS field projected into the Odoo bridge model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    label: str = Field(min_length=1, max_length=80)
    field_type: Literal["Boolean", "Char", "Date", "Datetime", "Float", "Integer", "Json", "Text"]
    required: bool = False
    index: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject unsafe, reserved, and non-canonical field names."""
        if not _FIELD_RE.fullmatch(value):
            raise ValueError("field name must match [a-z][a-z0-9_]*")
        if value in _RESERVED_FIELDS:
            raise ValueError(f"field name is reserved by the bridge protocol: {value}")
        return value


class BridgeActionSpec(BaseModel):
    """One generated, explicitly allowlisted Odoo-to-FMOS command."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    label: str = Field(min_length=1, max_length=80)
    command_type: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject names that cannot become fixed Odoo action methods."""
        if not _ACTION_RE.fullmatch(value):
            raise ValueError("action name must match [a-z][a-z0-9_]*")
        if value.startswith("_"):
            raise ValueError("action name cannot be private")
        return value

    @field_validator("command_type")
    @classmethod
    def validate_command_type(cls, value: str) -> str:
        """Require a stable, versioned command contract identifier."""
        if not _COMMAND_RE.fullmatch(value):
            raise ValueError("command_type must be a lowercase versioned identifier ending in .vN")
        return value


class BridgeSpec(BaseModel):
    """Complete deterministic input contract for one Odoo 19 bridge addon."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"]
    module_name: str
    module_title: str = Field(min_length=1, max_length=120)
    version: str
    provider_contract_id: str
    wrapper_contract_id: str
    projection_contract_id: str
    action_contract_id: str
    author: str = Field(min_length=1, max_length=120)
    license: Literal["LGPL-3", "OEEL-1", "OPL-1"] = "LGPL-3"
    bridge_mode: Literal["fmos_authoritative", "odoo_native_extension"]
    model_name: str
    domain: str
    fields: tuple[BridgeFieldSpec, ...] = Field(min_length=1, max_length=64)
    actions: tuple[BridgeActionSpec, ...] = Field(min_length=1, max_length=32)
    max_attempts: int = Field(default=8, ge=1, le=20)
    retry_base_seconds: int = Field(default=60, ge=5, le=3600)
    retry_cap_seconds: int = Field(default=3600, ge=60, le=86400)
    cron_batch_size: int = Field(default=100, ge=1, le=1000)

    @field_validator("fields", "actions", mode="before")
    @classmethod
    def freeze_json_arrays(cls, value: Any) -> tuple[Any, ...]:
        """Accept the JSON array wire shape, then freeze it for internal use."""
        if not isinstance(value, list):
            raise TypeError("fields and actions must be JSON arrays")
        return tuple(value)

    @field_validator("module_name")
    @classmethod
    def validate_module_name(cls, value: str) -> str:
        """Require an FMOS bridge module name with no path semantics."""
        if not _MODULE_RE.fullmatch(value):
            raise ValueError("module_name must match fm_[a-z][a-z0-9_]*_bridge")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        """Require an Odoo 19 five-part addon version."""
        if not _VERSION_RE.fullmatch(value):
            raise ValueError("version must be an Odoo 19 version such as 19.0.1.0.0")
        return value

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        """Require a stable domain key suitable for generated identifiers."""
        if not _FIELD_RE.fullmatch(value):
            raise ValueError("domain must match [a-z][a-z0-9_]*")
        return value

    @field_validator(
        "provider_contract_id",
        "wrapper_contract_id",
        "projection_contract_id",
        "action_contract_id",
    )
    @classmethod
    def validate_contract_id(cls, value: str) -> str:
        """Require stable lowercase versioned bridge contract identifiers."""
        if not _COMMAND_RE.fullmatch(value):
            raise ValueError("contract IDs must be lowercase versioned identifiers ending in .vN")
        return value

    @model_validator(mode="after")
    def validate_protocol_contract(self) -> BridgeSpec:
        """Fail closed on ambiguous ownership, names, and retry policy."""
        if self.bridge_mode == "fmos_authoritative":
            if not _MODEL_RE.fullmatch(self.model_name):
                raise ValueError("FMOS-authoritative model_name must start with fm. and use lowercase segments")
        else:
            if self.model_name not in ODOO_NATIVE_TARGETS:
                allowed = ", ".join(sorted(ODOO_NATIVE_TARGETS))
                raise ValueError(f"Odoo-native extension model is not allowlisted; expected one of: {allowed}")

        expected_fragment = f"_{self.domain}_"
        if expected_fragment not in f"_{self.module_name}_":
            raise ValueError("module_name must include the declared domain")

        field_names = [field.name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("field names must be unique")

        action_names = [action.name for action in self.actions]
        if len(action_names) != len(set(action_names)):
            raise ValueError("action names must be unique")

        command_types = [action.command_type for action in self.actions]
        if len(command_types) != len(set(command_types)):
            raise ValueError("command types must be unique")

        contract_ids = (
            self.provider_contract_id,
            self.wrapper_contract_id,
            self.projection_contract_id,
            self.action_contract_id,
        )
        if len(set(contract_ids)) != len(contract_ids):
            raise ValueError("provider, wrapper, projection, and action contract IDs must be distinct")

        if self.retry_cap_seconds < self.retry_base_seconds:
            raise ValueError("retry_cap_seconds must be greater than or equal to retry_base_seconds")
        return self


def parse_bridge_spec(raw: Mapping[str, Any]) -> BridgeSpec:
    """Validate an in-memory declarative bridge spec without mutating it."""
    return BridgeSpec.model_validate(dict(raw))


def load_bridge_spec(spec_path: Path, *, allowed_root: Path) -> BridgeSpec:
    """Load one relative JSON spec while rejecting traversal and symlink escapes.

    Args:
        spec_path: Relative path to the JSON spec below ``allowed_root``.
        allowed_root: Exact directory authorized to contain the input.

    Returns:
        Fully validated immutable bridge spec.

    Raises:
        ValueError: If the path or JSON shape is unsafe.
        FileNotFoundError: If the authorized spec does not exist.
    """
    if spec_path.is_absolute():
        raise ValueError("spec_path must be relative to allowed_root")
    if spec_path.suffix != ".json":
        raise ValueError("bridge specs must use the .json extension")

    root = allowed_root.resolve(strict=True)
    candidate = allowed_root / spec_path
    cursor = allowed_root
    for part in spec_path.parts:
        if part in {"", ".", ".."}:
            raise ValueError("spec_path contains an unsafe path segment")
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("bridge spec paths cannot traverse symlinks")

    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("spec_path escapes allowed_root")
    if not resolved.is_file():
        raise ValueError("bridge spec path must identify a regular file")
    if resolved.stat().st_size > 1_000_000:
        raise ValueError("bridge spec exceeds the 1 MB input limit")

    raw_text = resolved.read_text(encoding="utf-8")
    raw = json.loads(raw_text, object_pairs_hook=_reject_duplicate_json_keys)
    if not isinstance(raw, dict):
        raise TypeError("bridge spec root must be a JSON object")
    return parse_bridge_spec(raw)
