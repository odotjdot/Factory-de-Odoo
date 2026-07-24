"""Deterministic templates for read-only Odoo-native provider wrappers."""

from __future__ import annotations

import json
import textwrap

from amil_utils.odoo_bridge.native_readonly_schema import NativeReadOnlySpec


def _class_name(model_name: str) -> str:
    return "".join(part.capitalize() for part in model_name.replace(".", "_").split("_"))


def _render_models(spec: NativeReadOnlySpec) -> str:
    classes = []
    for model_name in spec.models:
        classes.append(
            textwrap.dedent(
                f'''
                class FmosReadOnly{_class_name(model_name)}(models.Model):
                    """Register {model_name} without changing its native behavior."""

                    _inherit = {model_name!r}
                '''
            ).strip()
        )
    return (
        '"""Generated read-only extensions of Odoo-authoritative models."""\n\n'
        "from odoo import models\n\n\n"
        + "\n\n\n".join(classes)
        + "\n"
    )


def render_native_readonly_files(spec: NativeReadOnlySpec) -> dict[str, str]:
    """Render the complete managed file set for one native wrapper."""
    manifest = {
        "name": spec.module_title,
        "version": spec.version,
        "author": spec.author,
        "license": spec.license,
        "category": "Operations/FMOS",
        "summary": "Generated read-only registration wrapper for Odoo-authoritative models",
        "depends": list(spec.dependencies),
        "data": [],
        "installable": True,
        "application": False,
        "auto_install": False,
    }
    install_contract = {
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
    }
    model_lines = "\n".join(f"- ``{model}``" for model in spec.models)
    readme = (
        f"{spec.module_title}\n"
        f"{'=' * len(spec.module_title)}\n\n"
        f"Factory-generated install wrapper for ``{spec.provider_contract_id}``.\n"
        "Odoo is the sole authoritative writer. This addon adds no fields, write\n"
        "methods, commands, action methods, ACL grants, projections, or outboxes.\n\n"
        "Registered native models\n"
        "------------------------\n\n"
        f"{model_lines}\n"
    )
    tests = textwrap.dedent(
        f'''
        """Runtime installability tests for the generated native wrapper."""

        from odoo.tests import tagged
        from odoo.tests.common import TransactionCase


        @tagged("post_install", "-at_install")
        class Test{_class_name(spec.module_name)}(TransactionCase):
            def test_exact_native_models_are_registered(self):
                self.assertEqual(
                    {tuple(spec.models)!r},
                    tuple(self.env[model]._name for model in {tuple(spec.models)!r}),
                )

            def test_wrapper_adds_no_allowlisted_actions(self):
                module = self.env["ir.module.module"].search(
                    [("name", "=", {spec.module_name!r})],
                    limit=1,
                )
                self.assertEqual(module.state, "installed")
        '''
    ).strip() + "\n"
    return {
        "__init__.py": "from . import models\n",
        "__manifest__.py": repr(manifest) + "\n",
        "models/__init__.py": "from . import native_models\n",
        "models/native_models.py": _render_models(spec),
        "tests/__init__.py": "from . import test_native_readonly\n",
        "tests/test_native_readonly.py": tests,
        "README.rst": readme,
        "INSTALL_CONTRACT.json": json.dumps(install_contract, indent=2, sort_keys=True) + "\n",
    }
