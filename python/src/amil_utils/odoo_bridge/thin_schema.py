"""Schema v2 for modular FMOS-authoritative thin Odoo addon families."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amil_utils.odoo_bridge.schema import BridgeFieldSpec

_MODULE_RE = re.compile(r"fm_[a-z][a-z0-9_]*\Z")
_MODEL_RE = re.compile(r"fm\.[a-z][a-z0-9_.]*\Z")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_COMMAND_RE = re.compile(r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*\Z")
_VERSION_RE = re.compile(r"19\.0\.[1-9][0-9]*\.[0-9]+\.[0-9]+\Z")
_SENSITIVE_TOKENS = frozenset(
    {
        "address",
        "actor",
        "avatar",
        "bio",
        "cognito",
        "contact",
        "customer_email",
        "customer_name",
        "customer_phone",
        "ein",
        "email",
        "gps",
        "legal_name",
        "message",
        "name",
        "note",
        "operator",
        "password",
        "payload",
        "person",
        "phone",
        "secret",
        "stripe",
        "token",
    }
)


def _validate_safe_data_name(value: str, label: str) -> str:
    if not _NAME_RE.fullmatch(value):
        raise ValueError(f"{label} must match [a-z][a-z0-9_]*")
    parts = set(value.split("_"))
    if value in _SENSITIVE_TOKENS or parts & _SENSITIVE_TOKENS:
        raise ValueError(f"{label} contains a sensitive or operator-data term: {value}")
    return value


class ThinActionSpec(BaseModel):
    """One fixed Odoo action and its closed payload vocabulary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    label: str = Field(min_length=1, max_length=80)
    command_type: str
    payload_fields: tuple[str, ...] = ()

    @field_validator("payload_fields", mode="before")
    @classmethod
    def freeze_payload_fields(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise TypeError("payload_fields must be a JSON array")
        return tuple(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_safe_data_name(value, "action name")

    @field_validator("command_type")
    @classmethod
    def validate_command_type(cls, value: str) -> str:
        if not _COMMAND_RE.fullmatch(value):
            raise ValueError("command_type must be lowercase, versioned, and end in .vN")
        return value

    @field_validator("payload_fields")
    @classmethod
    def validate_payload_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for field_name in value:
            _validate_safe_data_name(field_name, "action payload field")
        if len(value) != len(set(value)):
            raise ValueError("action payload fields must be unique")
        return value


class ProtocolActionSpec(ThinActionSpec):
    """One action registered by a protocol-owning addon."""

    aggregate_model: str

    @field_validator("aggregate_model")
    @classmethod
    def validate_aggregate_model(cls, value: str) -> str:
        if not _MODEL_RE.fullmatch(value):
            raise ValueError("aggregate_model must be a lowercase fm.* model")
        return value


class ThinConceptSpec(BaseModel):
    """One FMOS aggregate projection exposed by an addon."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    title: str = Field(min_length=1, max_length=120)
    model_name: str
    fields: tuple[BridgeFieldSpec, ...] = Field(min_length=1, max_length=64)
    actions: tuple[ThinActionSpec, ...] = Field(default=(), max_length=32)

    @field_validator("fields", "actions", mode="before")
    @classmethod
    def freeze_arrays(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, list):
            raise TypeError("concept fields and actions must be JSON arrays")
        return tuple(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_safe_data_name(value, "concept name")

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not _MODEL_RE.fullmatch(value):
            raise ValueError("model_name must be a lowercase fm.* model")
        return value

    @model_validator(mode="after")
    def validate_unique_members(self) -> ThinConceptSpec:
        field_names = [field.name for field in self.fields]
        for field_name in field_names:
            _validate_safe_data_name(field_name, "projection field")
        if len(field_names) != len(set(field_names)):
            raise ValueError("projection field names must be unique")
        action_names = [action.name for action in self.actions]
        command_types = [action.command_type for action in self.actions]
        if len(action_names) != len(set(action_names)):
            raise ValueError("concept action names must be unique")
        if len(command_types) != len(set(command_types)):
            raise ValueError("concept command types must be unique")
        return self


class ThinBridgeSpec(BaseModel):
    """Complete deterministic input for one modular thin Odoo addon."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["2.0"]
    module_name: str
    module_title: str = Field(min_length=1, max_length=120)
    version: str
    author: str = Field(min_length=1, max_length=120)
    license: Literal["LGPL-3", "OEEL-1", "OPL-1"] = "LGPL-3"
    provider_contract_id: str
    wrapper_contract_id: str
    projection_contract_id: str
    action_contract_id: str
    protocol_model_prefix: str
    role: Literal["protocol_base", "projection", "standalone"]
    base_module: str | None
    dependencies: tuple[str, ...] = ()
    concepts: tuple[ThinConceptSpec, ...]
    protocol_actions: tuple[ProtocolActionSpec, ...]
    max_attempts: int = Field(default=8, ge=1, le=20)
    retry_base_seconds: int = Field(default=60, ge=5, le=3600)
    retry_cap_seconds: int = Field(default=3600, ge=60, le=86400)
    cron_batch_size: int = Field(default=100, ge=1, le=1000)

    @field_validator("dependencies", "concepts", "protocol_actions", mode="before")
    @classmethod
    def freeze_arrays(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, list):
            raise TypeError("dependencies, concepts, and protocol_actions must be JSON arrays")
        return tuple(value)

    @field_validator("module_name")
    @classmethod
    def validate_module_name(cls, value: str) -> str:
        if not _MODULE_RE.fullmatch(value):
            raise ValueError("module_name must match fm_[a-z][a-z0-9_]*")
        return value

    @field_validator("base_module")
    @classmethod
    def validate_base_module(cls, value: str | None) -> str | None:
        if value is not None and not _MODULE_RE.fullmatch(value):
            raise ValueError("base_module must match fm_[a-z][a-z0-9_]*")
        return value

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _MODULE_RE.fullmatch(item) for item in value):
            raise ValueError("dependencies must contain canonical fm_* addon names")
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
        return value

    @field_validator("protocol_model_prefix")
    @classmethod
    def validate_protocol_prefix(cls, value: str) -> str:
        if not _MODEL_RE.fullmatch(value):
            raise ValueError("protocol_model_prefix must be a lowercase fm.* model prefix")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _VERSION_RE.fullmatch(value):
            raise ValueError("version must be an Odoo 19 five-part version")
        return value

    @field_validator(
        "provider_contract_id",
        "wrapper_contract_id",
        "projection_contract_id",
        "action_contract_id",
    )
    @classmethod
    def validate_contract(cls, value: str) -> str:
        if not _COMMAND_RE.fullmatch(value):
            raise ValueError("contract IDs must be lowercase versioned identifiers ending in .vN")
        return value

    @model_validator(mode="after")
    def validate_module_shape(self) -> ThinBridgeSpec:
        if self.retry_cap_seconds < self.retry_base_seconds:
            raise ValueError("retry_cap_seconds must be greater than or equal to retry_base_seconds")
        if self.role == "protocol_base":
            if self.base_module is not None or self.concepts:
                raise ValueError("protocol_base cannot declare a base_module or concepts")
        elif self.role == "projection":
            if self.base_module is None or not self.concepts or self.protocol_actions:
                raise ValueError("projection requires a base_module and concepts but no protocol_actions")
            expected_base = f"{self.protocol_model_prefix.replace('.', '_')}_base"
            if self.base_module != expected_base:
                raise ValueError(
                    f"base_module must be the canonical protocol owner {expected_base}"
                )
        elif self.base_module is not None or not self.concepts:
            raise ValueError("standalone requires concepts and cannot declare a base_module")

        concept_names = [concept.name for concept in self.concepts]
        model_names = [concept.model_name for concept in self.concepts]
        if len(concept_names) != len(set(concept_names)):
            raise ValueError("concept names must be unique")
        if len(model_names) != len(set(model_names)):
            raise ValueError("concept models must be unique")
        if any(not model.startswith(f"{self.protocol_model_prefix}.") for model in model_names):
            raise ValueError("concept models must be below protocol_model_prefix")

        protocol_keys = [
            (action.aggregate_model, action.name, action.command_type)
            for action in self.protocol_actions
        ]
        if len(protocol_keys) != len(set(protocol_keys)):
            raise ValueError("protocol action registrations must be unique")
        if self.role == "standalone":
            concept_actions = {
                (concept.model_name, action.name, action.command_type, action.payload_fields)
                for concept in self.concepts
                for action in concept.actions
            }
            registered = {
                (action.aggregate_model, action.name, action.command_type, action.payload_fields)
                for action in self.protocol_actions
            }
            if concept_actions != registered:
                raise ValueError("standalone protocol_actions must exactly match concept actions")
        return self


def parse_thin_bridge_spec(raw: dict[str, Any]) -> ThinBridgeSpec:
    """Validate an in-memory schema-v2 addon declaration."""
    return ThinBridgeSpec.model_validate(dict(raw))
