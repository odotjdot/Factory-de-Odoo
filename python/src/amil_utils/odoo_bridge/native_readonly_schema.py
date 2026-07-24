"""Strict schema for read-only Odoo-native provider extension wrappers."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MODULE_RE = re.compile(r"(?:fm_[a-z][a-z0-9_]*|fmos_headless_portal_[a-z][a-z0-9_]*)\Z")
_ODOO_NAME_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_MODEL_RE = re.compile(r"[a-z][a-z0-9_.]*\Z")
_METHOD_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_CONTRACT_RE = re.compile(r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*\Z")
_VERSION_RE = re.compile(r"19\.0\.[1-9][0-9]*\.[0-9]+\.[0-9]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RELATIVE_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\Z")


class NativeReadOnlySpec(BaseModel):
    """Complete deterministic input for one read-only Odoo-native wrapper."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"]
    module_name: str
    module_title: str = Field(min_length=1, max_length=120)
    version: str
    author: str = Field(min_length=1, max_length=120)
    license: Literal["LGPL-3", "OEEL-1", "OPL-1"] = "LGPL-3"
    provider_contract_id: str
    wrapper_contract_id: str
    aggregate_model: str
    models: tuple[str, ...] = Field(min_length=1, max_length=32)
    extended_models: tuple[str, ...] = Field(default=(), max_length=32)
    service_model: str | None = None
    service_methods: tuple[str, ...] = Field(default=(), max_length=64)
    dependencies: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator(
        "models",
        "extended_models",
        "service_methods",
        "dependencies",
        mode="before",
    )
    @classmethod
    def freeze_arrays(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise TypeError("models, service methods, and dependencies must be JSON arrays")
        return tuple(value)

    @field_validator("module_name")
    @classmethod
    def validate_module_name(cls, value: str) -> str:
        if not _MODULE_RE.fullmatch(value):
            raise ValueError("module_name must be a canonical fm_* or fmos_headless_portal_* addon")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _VERSION_RE.fullmatch(value):
            raise ValueError("version must be an Odoo 19 five-part version")
        return value

    @field_validator("provider_contract_id", "wrapper_contract_id")
    @classmethod
    def validate_contract(cls, value: str) -> str:
        if not _CONTRACT_RE.fullmatch(value):
            raise ValueError("contract IDs must be lowercase versioned identifiers ending in .vN")
        return value

    @field_validator("aggregate_model")
    @classmethod
    def validate_aggregate_model(cls, value: str) -> str:
        if not _MODEL_RE.fullmatch(value):
            raise ValueError("aggregate_model must be a canonical Odoo model")
        return value

    @field_validator("models", "extended_models")
    @classmethod
    def validate_models(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _MODEL_RE.fullmatch(model) for model in value):
            raise ValueError("models must contain canonical Odoo model names")
        if len(value) != len(set(value)):
            raise ValueError("models must be unique")
        return value

    @field_validator("service_model")
    @classmethod
    def validate_service_model(cls, value: str | None) -> str | None:
        if value is not None and not _MODEL_RE.fullmatch(value):
            raise ValueError("service_model must be a canonical Odoo model")
        return value

    @field_validator("service_methods")
    @classmethod
    def validate_service_methods(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _METHOD_RE.fullmatch(method) for method in value):
            raise ValueError("service_methods must contain fixed safe Odoo method names")
        if len(value) != len(set(value)):
            raise ValueError("service_methods must be unique")
        return value

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _ODOO_NAME_RE.fullmatch(module) for module in value):
            raise ValueError("dependencies must contain canonical Odoo addon names")
        if len(value) != len(set(value)):
            raise ValueError("dependencies must be unique")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> NativeReadOnlySpec:
        if self.aggregate_model not in self.models:
            raise ValueError("models must include aggregate_model")
        if self.provider_contract_id == self.wrapper_contract_id:
            raise ValueError("provider and wrapper contracts must be distinct")
        if set(self.models) & set(self.extended_models):
            raise ValueError("models and extended_models must be disjoint")
        if self.service_model is None and self.service_methods:
            raise ValueError("service_methods require service_model")
        if self.service_model is not None:
            if self.service_model not in self.extended_models:
                raise ValueError("service_model must be declared in extended_models")
            if not self.service_methods:
                raise ValueError("service_model requires at least one fixed service method")
        return self


class NativePreservedFileSpec(BaseModel):
    """One immutable pre-existing addon file retained by a generated seal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    sha256: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not _RELATIVE_PATH_RE.fullmatch(value)
            or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("preserved file path must be a safe relative path")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("preserved file sha256 must be lowercase SHA-256")
        return value


class NativeExistingReadOnlySpec(NativeReadOnlySpec):
    """Provider declaration sealing an existing native addon byte-for-byte."""

    generate_contract_tests: bool = True
    preserved_files: tuple[NativePreservedFileSpec, ...] = Field(min_length=1, max_length=512)

    @field_validator("preserved_files", mode="before")
    @classmethod
    def freeze_preserved_files(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, list):
            raise TypeError("preserved_files must be a JSON array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_preserved_files(self) -> NativeExistingReadOnlySpec:
        paths = [item.path for item in self.preserved_files]
        if len(paths) != len(set(paths)):
            raise ValueError("preserved file paths must be unique")
        generated = {
            ".amil-bridge.json",
            "INSTALL_CONTRACT.json",
            "bridge-spec.json",
        }
        if self.generate_contract_tests:
            generated.update({"tests/__init__.py", "tests/test_native_readonly.py"})
        if set(paths) & generated:
            raise ValueError("preserved files cannot claim generated seal paths")
        if (
            not self.generate_contract_tests
            and not any(path.startswith("tests/test_") and path.endswith(".py") for path in paths)
        ):
            raise ValueError("existing-domain seals must preserve executable Odoo tests")
        return self


# The existing-domain name is the canonical API for Odoo-owned modules with a
# fixed headless BFF. The read-only name remains as a compatibility alias.
NativeExistingDomainSpec = NativeExistingReadOnlySpec


def parse_native_readonly_spec(raw: dict[str, Any]) -> NativeReadOnlySpec:
    """Validate one in-memory read-only Odoo-native wrapper declaration."""
    return NativeReadOnlySpec.model_validate(dict(raw))


def parse_existing_native_readonly_spec(raw: dict[str, Any]) -> NativeExistingReadOnlySpec:
    """Validate one existing-addon read-only provider seal declaration."""
    return NativeExistingReadOnlySpec.model_validate(dict(raw))


def parse_existing_native_domain_spec(raw: dict[str, Any]) -> NativeExistingDomainSpec:
    """Validate one existing Odoo-owned domain plus fixed headless BFF seal."""
    return NativeExistingDomainSpec.model_validate(dict(raw))
