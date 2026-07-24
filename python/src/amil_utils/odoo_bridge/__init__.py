"""Deterministic Odoo 19 bridge-addon compiler for FMOS dual-mode domains."""

from amil_utils.odoo_bridge.renderer import (
    GeneratedAddon,
    GeneratedFile,
    materialize_bridge_addon,
    render_bridge_addon,
)
from amil_utils.odoo_bridge.native_readonly_renderer import (
    render_existing_native_domain_addon,
    render_existing_native_readonly_addon,
    render_native_readonly_addon,
)
from amil_utils.odoo_bridge.native_readonly_schema import (
    NativeExistingDomainSpec,
    NativeExistingReadOnlySpec,
    NativePreservedFileSpec,
    NativeReadOnlySpec,
    parse_existing_native_domain_spec,
    parse_existing_native_readonly_spec,
    parse_native_readonly_spec,
)
from amil_utils.odoo_bridge.schema import (
    BridgeActionSpec,
    BridgeFieldSpec,
    BridgeSpec,
    load_bridge_spec,
    parse_bridge_spec,
)
from amil_utils.odoo_bridge.thin_renderer import render_thin_bridge_addon
from amil_utils.odoo_bridge.thin_schema import (
    ProtocolActionSpec,
    ThinActionSpec,
    ThinBridgeSpec,
    ThinConceptSpec,
    parse_thin_bridge_spec,
)

__all__ = [
    "BridgeActionSpec",
    "BridgeFieldSpec",
    "BridgeSpec",
    "GeneratedAddon",
    "GeneratedFile",
    "NativeExistingReadOnlySpec",
    "NativeExistingDomainSpec",
    "NativePreservedFileSpec",
    "NativeReadOnlySpec",
    "ProtocolActionSpec",
    "ThinActionSpec",
    "ThinBridgeSpec",
    "ThinConceptSpec",
    "load_bridge_spec",
    "materialize_bridge_addon",
    "parse_bridge_spec",
    "parse_existing_native_domain_spec",
    "parse_existing_native_readonly_spec",
    "parse_native_readonly_spec",
    "parse_thin_bridge_spec",
    "render_bridge_addon",
    "render_existing_native_domain_addon",
    "render_existing_native_readonly_addon",
    "render_native_readonly_addon",
    "render_thin_bridge_addon",
]
