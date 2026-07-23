"""Deterministic Odoo 19 bridge-addon compiler for FMOS dual-mode domains."""

from amil_utils.odoo_bridge.renderer import (
    GeneratedAddon,
    GeneratedFile,
    materialize_bridge_addon,
    render_bridge_addon,
)
from amil_utils.odoo_bridge.schema import (
    BridgeActionSpec,
    BridgeFieldSpec,
    BridgeSpec,
    load_bridge_spec,
    parse_bridge_spec,
)

__all__ = [
    "BridgeActionSpec",
    "BridgeFieldSpec",
    "BridgeSpec",
    "GeneratedAddon",
    "GeneratedFile",
    "load_bridge_spec",
    "materialize_bridge_addon",
    "parse_bridge_spec",
    "render_bridge_addon",
]
