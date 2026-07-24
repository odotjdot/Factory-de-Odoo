"""Deterministic templates for schema-v2 modular thin Odoo addons."""

from __future__ import annotations

import json
import textwrap

from amil_utils.odoo_bridge.schema import BridgeFieldSpec
from amil_utils.odoo_bridge.thin_schema import ThinActionSpec, ThinBridgeSpec, ThinConceptSpec

AUTHORITY_FIELDS = (
    "network_id",
    "business_id",
    "app_id",
    "channel_id",
    "identity_id",
    "actor_id",
    "company_id",
    "install_id",
)


def _clean(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


def _class_name(value: str) -> str:
    return "".join(part.capitalize() for part in value.replace(".", "_").split("_"))


def _model_ref(model: str) -> str:
    return f"model_{model.replace('.', '_')}"


def _protocol_models(spec: ThinBridgeSpec) -> dict[str, str]:
    prefix = spec.protocol_model_prefix
    return {
        "mixin": f"{prefix}.projection.mixin",
        "crosswalk": f"{prefix}.crosswalk",
        "projection_receipt": f"{prefix}.projection.receipt",
        "action_receipt": f"{prefix}.action.receipt",
        "outbox": f"{prefix}.command.outbox",
    }


def _dependencies(spec: ThinBridgeSpec) -> list[str]:
    dependencies = ["base", "mail", *spec.dependencies]
    if spec.base_module:
        dependencies.append(spec.base_module)
    return list(dict.fromkeys(dependencies))


def _field_line(field: BridgeFieldSpec) -> str:
    options = [f"string={field.label!r}"]
    if field.required:
        options.append("required=True")
    if field.index:
        options.append("index=True")
    if field.field_type in {"Char", "Text"}:
        options.extend(("readonly=True", "tracking=True"))
    else:
        options.extend(("readonly=True", "tracking=True"))
    return f"    {field.name} = fields.{field.field_type}({', '.join(options)})"


def _action_method(action: ThinActionSpec) -> str:
    return _clean(
        f'''
        def action_{action.name}(
            self,
            *,
            idempotency_key,
            correlation_id,
            expected_canonical_version,
            payload=None,
        ):
            """Queue the fixed {action.name} FMOS command."""
            return self._capture_fmos_action(
                action={action.name!r},
                command_type={action.command_type!r},
                payload_fields={action.payload_fields!r},
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                expected_canonical_version=expected_canonical_version,
                payload=payload or {{}},
            )
        '''
    ).rstrip()


def _render_manifest(spec: ThinBridgeSpec) -> str:
    data = [
        "security/groups.xml" if spec.role != "projection" else None,
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/cron.xml" if spec.role != "projection" else None,
        "views/protocol_views.xml" if spec.role != "projection" else None,
        "views/projection_views.xml" if spec.concepts else None,
        "views/menu.xml",
    ]
    payload = {
        "name": spec.module_title,
        "version": spec.version,
        "author": spec.author,
        "license": spec.license,
        "category": "Operations/FMOS",
        "summary": "Generated FMOS-authoritative thin Odoo projection and command seam",
        "depends": _dependencies(spec),
        "data": [item for item in data if item],
        "installable": True,
        "application": False,
        "auto_install": False,
    }
    return repr(payload) + "\n"


def _render_root_init() -> str:
    return "from . import models\n"


def _render_models_init(spec: ThinBridgeSpec) -> str:
    imports = []
    if spec.role != "projection":
        imports.append("from . import protocol")
    if spec.concepts:
        imports.append("from . import projections")
    return "\n".join(imports) + "\n"


def _render_protocol(spec: ThinBridgeSpec) -> str:
    models = _protocol_models(spec)
    class_prefix = _class_name(spec.protocol_model_prefix)
    registry = {
        (action.aggregate_model, action.name): {
            "command_type": action.command_type,
            "payload_fields": action.payload_fields,
        }
        for action in spec.protocol_actions
    }
    return _clean(
        f'''
        """Generated shared FMOS projection, receipt, crosswalk, and outbox protocol."""

        import hashlib
        import json
        import logging
        import re
        import uuid
        from datetime import timedelta

        from odoo import api, fields, models
        from odoo.exceptions import AccessError, UserError, ValidationError

        _logger = logging.getLogger(__name__)
        _AUTHORITY_FIELDS = {AUTHORITY_FIELDS!r}
        _ACTION_REGISTRY = {registry!r}
        _IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{{7,127}}\\Z")
        _FINGERPRINT_RE = re.compile(r"[0-9a-f]{{64}}\\Z")
        _MAX_ATTEMPTS = {spec.max_attempts}
        _RETRY_BASE_SECONDS = {spec.retry_base_seconds}
        _RETRY_CAP_SECONDS = {spec.retry_cap_seconds}
        _CRON_BATCH_SIZE = {spec.cron_batch_size}
        _LEASE_SECONDS = 900


        def _stable_fingerprint(value):
            try:
                encoded = json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
            except (TypeError, ValueError) as exc:
                raise ValidationError("Bridge values must be JSON serializable.") from exc
            return hashlib.sha256(encoded).hexdigest()


        class {class_prefix}ProjectionMixin(models.AbstractModel):
            _name = {models["mixin"]!r}
            _description = {f"{spec.module_title} Projection Protocol"!r}
            _check_company_auto = True

            company_id = fields.Many2one(
                "res.company",
                required=True,
                default=lambda self: self.env.company,
                index=True,
                readonly=True,
            )
            network_id = fields.Char(required=True, index=True, readonly=True)
            business_id = fields.Char(required=True, index=True, readonly=True)
            app_id = fields.Char(required=True, index=True, readonly=True)
            channel_id = fields.Char(required=True, index=True, readonly=True)
            identity_id = fields.Char(required=True, index=True, readonly=True)
            actor_id = fields.Char(required=True, index=True, readonly=True)
            install_id = fields.Char(required=True, index=True, readonly=True)
            canonical_id = fields.Char(required=True, index=True, readonly=True)
            canonical_version = fields.Integer(required=True, readonly=True)
            fingerprint = fields.Char(required=True, readonly=True)
            origin = fields.Selection(
                (("fmos", "FMOS"), ("reconciler", "Reconciler")),
                required=True,
                readonly=True,
            )
            correlation_id = fields.Char(required=True, index=True, readonly=True)
            causation_id = fields.Char(index=True, readonly=True)
            last_projection_at = fields.Datetime(required=True, readonly=True)
            reconciliation_state = fields.Selection(
                (
                    ("pending", "Pending"),
                    ("in_sync", "In Sync"),
                    ("diverged", "Diverged"),
                    ("error", "Error"),
                ),
                required=True,
                default="pending",
                index=True,
                readonly=True,
            )
            source_version = fields.Integer(required=True, default=0, readonly=True)
            destination_version = fields.Integer(required=True, default=0, readonly=True)
            source_fingerprint = fields.Char(readonly=True)
            destination_fingerprint = fields.Char(readonly=True)
            last_reconciled_at = fields.Datetime(readonly=True)
            last_reconciliation_error_code = fields.Char(readonly=True)
            reconciliation_lag = fields.Integer(required=True, default=0, readonly=True)
            reconciliation_retry_count = fields.Integer(required=True, default=0, readonly=True)
            reconciliation_dead_letter_count = fields.Integer(required=True, default=0, readonly=True)

            @api.model_create_multi
            def create(self, values_list):
                if not self.env.context.get("fm_bridge_projection"):
                    raise AccessError(self.env._("Canonical projections can only be created by the FMOS seam."))
                return super().create(values_list)

            def write(self, values):
                protected = set(_AUTHORITY_FIELDS) | {{
                    "canonical_id",
                    "canonical_version",
                    "fingerprint",
                    "origin",
                }}
                if set(values) & protected and not self.env.context.get("fm_bridge_projection"):
                    raise AccessError(self.env._("Canonical projection identity is immutable outside the FMOS seam."))
                return super().write(values)

            @api.model
            def _require_operator(self):
                if not self.env.user.has_group({f"{spec.module_name}.group_bridge_operator"!r}) and not (
                    {spec.role == "projection"!r}
                    and self.env.user.has_group({f"{spec.base_module}.group_bridge_operator"!r})
                ):
                    raise AccessError(self.env._("Bridge operator access is required."))

            @api.model
            def _validate_scope(self, authority_scope):
                if not isinstance(authority_scope, dict) or set(authority_scope) != set(_AUTHORITY_FIELDS):
                    raise ValidationError(self.env._("Authority scope must contain every exact authority field."))
                company_id = authority_scope["company_id"]
                if not isinstance(company_id, int) or isinstance(company_id, bool):
                    raise ValidationError(self.env._("company_id must be an integer."))
                if company_id not in self.env.companies.ids:
                    raise AccessError(self.env._("Company is outside the active company scope."))
                normalized = {{"company_id": company_id}}
                for field_name in _AUTHORITY_FIELDS:
                    if field_name == "company_id":
                        continue
                    value = authority_scope[field_name]
                    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                        raise ValidationError(
                            self.env._("%(field)s is not a valid authority identifier.", field=field_name)
                        )
                    normalized[field_name] = value
                return normalized

            @api.model
            def _authority_domain(self, authority):
                return [(field_name, "=", authority[field_name]) for field_name in _AUTHORITY_FIELDS]

            @api.model
            def _lock(self, authority, aggregate_model, canonical_id):
                digest = hashlib.sha256(
                    json.dumps(
                        {{
                            "authority": authority,
                            "aggregate_model": aggregate_model,
                            "canonical_id": canonical_id,
                        }},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).digest()
                keys = (
                    int.from_bytes(digest[:4], "big", signed=True),
                    int.from_bytes(digest[4:8], "big", signed=True),
                )
                self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", keys)

            @api.model
            def _projection_fingerprint(self, authority_scope, canonical_id, canonical_version, values):
                return _stable_fingerprint(
                    {{
                        "authority_scope": authority_scope,
                        "aggregate_model": self._name,
                        "canonical_id": canonical_id,
                        "canonical_version": canonical_version,
                        "values": values,
                    }}
                )

            @api.model
            def apply_fmos_projection(
                self,
                *,
                authority_scope,
                canonical_id,
                canonical_version,
                fingerprint,
                values,
                origin,
                correlation_id,
                causation_id=None,
            ):
                """Apply one monotonic projection and immutable delivery receipt."""
                self._require_operator()
                authority = self._validate_scope(authority_scope)
                if origin not in ("fmos", "reconciler"):
                    raise ValidationError(self.env._("Odoo-origin projections are rejected to prevent loops."))
                for value, label in (
                    (canonical_id, "canonical_id"),
                    (correlation_id, "correlation_id"),
                ):
                    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                        raise ValidationError(self.env._("%(field)s is invalid.", field=label))
                if causation_id is not None and (
                    not isinstance(causation_id, str) or not _IDENTIFIER_RE.fullmatch(causation_id)
                ):
                    raise ValidationError(self.env._("causation_id is invalid."))
                if not isinstance(canonical_version, int) or isinstance(canonical_version, bool) or canonical_version < 1:
                    raise ValidationError(self.env._("canonical_version must be positive."))
                if not isinstance(values, dict):
                    raise ValidationError(self.env._("Projection values must be an object."))
                allowed = set(self._projection_fields())
                unknown = sorted(set(values) - allowed)
                if unknown:
                    raise ValidationError(
                        self.env._("Projection contains non-allowlisted fields: %(fields)s", fields=", ".join(unknown))
                    )
                expected = self._projection_fingerprint(
                    authority,
                    canonical_id,
                    canonical_version,
                    values,
                )
                if fingerprint != expected or not _FINGERPRINT_RE.fullmatch(fingerprint):
                    raise ValidationError(self.env._("Projection fingerprint conflict."))
                self._lock(authority, self._name, canonical_id)
                company = self.env["res.company"].browse(authority["company_id"]).exists()
                if not company:
                    raise ValidationError(self.env._("Projection company does not exist."))
                scoped = self.sudo().with_company(company).with_context(fm_bridge_projection=True)
                domain = self._authority_domain(authority) + [("canonical_id", "=", canonical_id)]
                record = scoped.search(domain, limit=1)
                if record and canonical_version < record.canonical_version:
                    raise ValidationError(self.env._("Projection version conflict: stale delivery."))
                if record and canonical_version == record.canonical_version:
                    if fingerprint != record.fingerprint:
                        raise ValidationError(self.env._("Projection version fingerprint conflict."))
                    return record
                projected = dict(values)
                projected.update(
                    {{
                        **authority,
                        "canonical_id": canonical_id,
                        "canonical_version": canonical_version,
                        "fingerprint": fingerprint,
                        "origin": origin,
                        "correlation_id": correlation_id,
                        "causation_id": causation_id,
                        "last_projection_at": fields.Datetime.now(),
                        "reconciliation_state": "in_sync",
                        "source_version": canonical_version,
                        "destination_version": canonical_version,
                        "source_fingerprint": fingerprint,
                        "destination_fingerprint": fingerprint,
                        "last_reconciled_at": fields.Datetime.now(),
                        "reconciliation_lag": 0,
                    }}
                )
                if record:
                    record.write(projected)
                else:
                    record = scoped.create(projected)
                self.env[{models["crosswalk"]!r}].sudo()._record_projection(record)
                self.env[{models["projection_receipt"]!r}].sudo()._record_projection(
                    record,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                record.message_post(
                    body=self.env._(
                        "FMOS projection v%(version)s applied; correlation %(correlation)s.",
                        version=canonical_version,
                        correlation=correlation_id,
                    ),
                    subtype_xmlid="mail.mt_note",
                )
                return record

            def _capture_fmos_action(
                self,
                *,
                action,
                command_type,
                payload_fields,
                idempotency_key,
                correlation_id,
                expected_canonical_version,
                payload,
            ):
                self.ensure_one()
                self._require_operator()
                receipt = self.env[{models["action_receipt"]!r}].sudo()._capture_action(
                    aggregate=self,
                    action=action,
                    command_type=command_type,
                    payload_fields=payload_fields,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    expected_canonical_version=expected_canonical_version,
                    payload=payload,
                )
                self.message_post(
                    body=self.env._(
                        "Allowlisted FMOS action %(action)s queued; correlation %(correlation)s.",
                        action=action,
                        correlation=correlation_id,
                    ),
                    subtype_xmlid="mail.mt_note",
                )
                return receipt

            @api.model
            def apply_reconciliation_observation(
                self,
                *,
                authority_scope,
                canonical_id,
                source_version,
                source_fingerprint,
                destination_version,
                destination_fingerprint,
                correlation_id,
            ):
                authority = self._validate_scope(authority_scope)
                self._lock(authority, self._name, canonical_id)
                record = self.sudo().with_context(fm_bridge_projection=True).search(
                    self._authority_domain(authority) + [("canonical_id", "=", canonical_id)],
                    limit=1,
                )
                if not record:
                    raise ValidationError(self.env._("Reconciliation aggregate does not exist."))
                in_sync = source_version == destination_version and source_fingerprint == destination_fingerprint
                record.write(
                    {{
                        "reconciliation_state": "in_sync" if in_sync else "diverged",
                        "source_version": source_version,
                        "destination_version": destination_version,
                        "source_fingerprint": source_fingerprint,
                        "destination_fingerprint": destination_fingerprint,
                        "last_reconciled_at": fields.Datetime.now(),
                        "reconciliation_lag": abs(source_version - destination_version),
                        "correlation_id": correlation_id,
                    }}
                )
                return record


        class {class_prefix}Crosswalk(models.Model):
            _name = {models["crosswalk"]!r}
            _description = {f"{spec.module_title} Canonical ID Crosswalk"!r}
            _check_company_auto = True
            _order = "aggregate_model, canonical_id"

            company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
            network_id = fields.Char(required=True, index=True, readonly=True)
            business_id = fields.Char(required=True, index=True, readonly=True)
            app_id = fields.Char(required=True, index=True, readonly=True)
            channel_id = fields.Char(required=True, index=True, readonly=True)
            identity_id = fields.Char(required=True, index=True, readonly=True)
            actor_id = fields.Char(required=True, index=True, readonly=True)
            install_id = fields.Char(required=True, index=True, readonly=True)
            aggregate_model = fields.Char(required=True, index=True, readonly=True)
            canonical_id = fields.Char(required=True, index=True, readonly=True)
            odoo_model = fields.Char(required=True, index=True, readonly=True)
            odoo_res_id = fields.Integer(required=True, index=True, readonly=True)
            canonical_version = fields.Integer(required=True, readonly=True)
            fingerprint = fields.Char(required=True, readonly=True)
            last_projected_at = fields.Datetime(required=True, readonly=True)

            _canonical_identity = models.UniqueIndex(
                "(company_id, network_id, business_id, app_id, channel_id, identity_id, "
                "actor_id, install_id, aggregate_model, canonical_id)"
            )

            def write(self, values):
                if not self.env.context.get("fm_bridge_projection"):
                    raise AccessError(self.env._("Crosswalks are maintained only by the projection seam."))
                return super().write(values)

            def unlink(self):
                raise AccessError(self.env._("Canonical crosswalks are immutable audit infrastructure."))

            @api.model
            def _record_projection(self, aggregate):
                values = {{
                    field_name: aggregate.company_id.id
                    if field_name == "company_id"
                    else aggregate[field_name]
                    for field_name in _AUTHORITY_FIELDS
                }}
                values.update(
                    {{
                        "aggregate_model": aggregate._name,
                        "canonical_id": aggregate.canonical_id,
                        "odoo_model": aggregate._name,
                        "odoo_res_id": aggregate.id,
                        "canonical_version": aggregate.canonical_version,
                        "fingerprint": aggregate.fingerprint,
                        "last_projected_at": fields.Datetime.now(),
                    }}
                )
                domain = [
                    (key, "=", value)
                    for key, value in values.items()
                    if key in _AUTHORITY_FIELDS or key in ("aggregate_model", "canonical_id")
                ]
                crosswalk = self.sudo().search(domain, limit=1)
                scoped = self.sudo().with_context(fm_bridge_projection=True)
                if crosswalk:
                    crosswalk.with_context(fm_bridge_projection=True).write(values)
                    return crosswalk
                return scoped.create(values)


        class {class_prefix}ProjectionReceipt(models.Model):
            _name = {models["projection_receipt"]!r}
            _description = {f"{spec.module_title} Immutable Projection Receipt"!r}
            _order = "applied_at desc, id desc"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
            network_id = fields.Char(required=True, index=True, readonly=True)
            business_id = fields.Char(required=True, index=True, readonly=True)
            app_id = fields.Char(required=True, index=True, readonly=True)
            channel_id = fields.Char(required=True, index=True, readonly=True)
            identity_id = fields.Char(required=True, index=True, readonly=True)
            actor_id = fields.Char(required=True, index=True, readonly=True)
            install_id = fields.Char(required=True, index=True, readonly=True)
            aggregate_model = fields.Char(required=True, index=True, readonly=True)
            aggregate_record_id = fields.Integer(required=True, index=True, readonly=True)
            canonical_id = fields.Char(required=True, index=True, readonly=True)
            canonical_version = fields.Integer(required=True, index=True, readonly=True)
            fingerprint = fields.Char(required=True, readonly=True)
            origin = fields.Char(required=True, readonly=True)
            correlation_id = fields.Char(required=True, index=True, readonly=True)
            causation_id = fields.Char(index=True, readonly=True)
            applied_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)

            _delivery_identity = models.UniqueIndex(
                "(company_id, network_id, business_id, app_id, channel_id, identity_id, "
                "actor_id, install_id, aggregate_model, canonical_id, canonical_version)"
            )

            def write(self, values):
                raise AccessError(self.env._("Projection receipts are immutable."))

            def unlink(self):
                raise AccessError(self.env._("Projection receipts are immutable."))

            @api.model
            def _record_projection(self, aggregate, *, correlation_id, causation_id):
                values = {{
                    field_name: aggregate.company_id.id
                    if field_name == "company_id"
                    else aggregate[field_name]
                    for field_name in _AUTHORITY_FIELDS
                }}
                values.update(
                    {{
                        "aggregate_model": aggregate._name,
                        "aggregate_record_id": aggregate.id,
                        "canonical_id": aggregate.canonical_id,
                        "canonical_version": aggregate.canonical_version,
                        "fingerprint": aggregate.fingerprint,
                        "origin": aggregate.origin,
                        "correlation_id": correlation_id,
                        "causation_id": causation_id,
                    }}
                )
                domain = [
                    (key, "=", value)
                    for key, value in values.items()
                    if key in _AUTHORITY_FIELDS
                    or key in ("aggregate_model", "canonical_id", "canonical_version")
                ]
                existing = self.sudo().search(domain, limit=1)
                if existing:
                    if existing.fingerprint != aggregate.fingerprint:
                        raise ValidationError(self.env._("Projection receipt fingerprint conflict."))
                    return existing
                return self.sudo().create(values)


        class {class_prefix}ActionReceipt(models.Model):
            _name = {models["action_receipt"]!r}
            _description = {f"{spec.module_title} Immutable Action Receipt"!r}
            _order = "created_at desc, id desc"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
            network_id = fields.Char(required=True, index=True, readonly=True)
            business_id = fields.Char(required=True, index=True, readonly=True)
            app_id = fields.Char(required=True, index=True, readonly=True)
            channel_id = fields.Char(required=True, index=True, readonly=True)
            identity_id = fields.Char(required=True, index=True, readonly=True)
            actor_id = fields.Char(required=True, index=True, readonly=True)
            install_id = fields.Char(required=True, index=True, readonly=True)
            aggregate_model = fields.Char(required=True, index=True, readonly=True)
            aggregate_record_id = fields.Integer(required=True, index=True, readonly=True)
            aggregate_canonical_id = fields.Char(required=True, index=True, readonly=True)
            expected_canonical_version = fields.Integer(required=True, readonly=True)
            aggregate_fingerprint = fields.Char(required=True, readonly=True)
            action = fields.Char(required=True, index=True, readonly=True)
            action_contract_id = fields.Char(required=True, readonly=True)
            command_type = fields.Char(required=True, index=True, readonly=True)
            payload = fields.Json(
                required=False,
                readonly=True,
                help="Null is the canonical representation of an allowlisted empty payload.",
            )
            idempotency_key = fields.Char(required=True, index=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            correlation_id = fields.Char(required=True, index=True, readonly=True)
            causation_id = fields.Char(required=True, index=True, readonly=True)
            created_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)

            _idempotency_identity = models.UniqueIndex("(company_id, idempotency_key)")

            def write(self, values):
                raise AccessError(self.env._("Action receipts are immutable."))

            def unlink(self):
                raise AccessError(self.env._("Action receipts are immutable."))

            @api.model
            def _capture_action(
                self,
                *,
                aggregate,
                action,
                command_type,
                payload_fields,
                idempotency_key,
                correlation_id,
                expected_canonical_version,
                payload,
            ):
                aggregate.ensure_one()
                registration = _ACTION_REGISTRY.get((aggregate._name, action))
                expected_registration = {{
                    "command_type": command_type,
                    "payload_fields": tuple(payload_fields),
                }}
                if registration != expected_registration:
                    raise ValidationError(self.env._("Action is not registered by the protocol allowlist."))
                if not isinstance(payload, dict) or set(payload) - set(payload_fields):
                    raise ValidationError(self.env._("Action payload contains non-allowlisted fields."))
                if expected_canonical_version != aggregate.canonical_version:
                    raise ValidationError(self.env._("Expected canonical version conflicts with the aggregate."))
                for value, label in (
                    (idempotency_key, "idempotency_key"),
                    (correlation_id, "correlation_id"),
                ):
                    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                        raise ValidationError(self.env._("%(field)s is invalid.", field=label))
                material = {{
                    "authority_scope": {{
                        field_name: aggregate.company_id.id
                        if field_name == "company_id"
                        else aggregate[field_name]
                        for field_name in _AUTHORITY_FIELDS
                    }},
                    "aggregate_model": aggregate._name,
                    "aggregate_canonical_id": aggregate.canonical_id,
                    "expected_canonical_version": expected_canonical_version,
                    "aggregate_fingerprint": aggregate.fingerprint,
                    "action": action,
                    "action_contract_id": {spec.action_contract_id!r},
                    "command_type": command_type,
                    "payload": payload,
                }}
                semantic_fingerprint = _stable_fingerprint(material)
                aggregate._lock(material["authority_scope"], aggregate._name, idempotency_key)
                existing = self.sudo().search(
                    [
                        ("company_id", "=", aggregate.company_id.id),
                        ("idempotency_key", "=", idempotency_key),
                    ],
                    limit=1,
                )
                if existing:
                    if existing.semantic_fingerprint != semantic_fingerprint:
                        raise ValidationError(self.env._("Action idempotency fingerprint conflict."))
                    return existing
                values = {{
                    field_name: aggregate.company_id.id
                    if field_name == "company_id"
                    else aggregate[field_name]
                    for field_name in _AUTHORITY_FIELDS
                }}
                values.update(
                    {{
                        "aggregate_model": aggregate._name,
                        "aggregate_record_id": aggregate.id,
                        "aggregate_canonical_id": aggregate.canonical_id,
                        "expected_canonical_version": expected_canonical_version,
                        "aggregate_fingerprint": aggregate.fingerprint,
                        "action": action,
                        "action_contract_id": {spec.action_contract_id!r},
                        "command_type": command_type,
                        "payload": payload,
                        "idempotency_key": idempotency_key,
                        "semantic_fingerprint": semantic_fingerprint,
                        "correlation_id": correlation_id,
                        "causation_id": aggregate.correlation_id,
                    }}
                )
                receipt = self.sudo().create(values)
                self.env[{models["outbox"]!r}].sudo()._enqueue_receipt(receipt)
                return receipt


        class {class_prefix}CommandOutbox(models.Model):
            _name = {models["outbox"]!r}
            _description = {f"{spec.module_title} FMOS Command Outbox"!r}
            _order = "next_attempt_at, id"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
            network_id = fields.Char(required=True, index=True, readonly=True)
            business_id = fields.Char(required=True, index=True, readonly=True)
            app_id = fields.Char(required=True, index=True, readonly=True)
            channel_id = fields.Char(required=True, index=True, readonly=True)
            identity_id = fields.Char(required=True, index=True, readonly=True)
            actor_id = fields.Char(required=True, index=True, readonly=True)
            install_id = fields.Char(required=True, index=True, readonly=True)
            receipt_id = fields.Many2one(
                {models["action_receipt"]!r},
                required=True,
                ondelete="restrict",
                check_company=True,
                index=True,
                readonly=True,
            )
            aggregate_model = fields.Char(required=True, index=True, readonly=True)
            aggregate_record_id = fields.Integer(required=True, index=True, readonly=True)
            aggregate_canonical_id = fields.Char(required=True, index=True, readonly=True)
            expected_canonical_version = fields.Integer(required=True, readonly=True)
            aggregate_fingerprint = fields.Char(required=True, readonly=True)
            action_contract_id = fields.Char(required=True, readonly=True)
            command_type = fields.Char(required=True, index=True, readonly=True)
            payload = fields.Json(
                required=False,
                readonly=True,
                help="Null is the canonical representation of an allowlisted empty payload.",
            )
            idempotency_key = fields.Char(required=True, index=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            correlation_id = fields.Char(required=True, index=True, readonly=True)
            causation_id = fields.Char(required=True, index=True, readonly=True)
            state = fields.Selection(
                (
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("retry", "Retry"),
                    ("done", "Done"),
                    ("dead_letter", "Dead Letter"),
                ),
                required=True,
                default="pending",
                index=True,
            )
            attempts = fields.Integer(required=True, default=0, readonly=True)
            max_attempts = fields.Integer(required=True, default=_MAX_ATTEMPTS, readonly=True)
            next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True, readonly=True)
            queued_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
            last_attempt_at = fields.Datetime(readonly=True)
            processing_started_at = fields.Datetime(readonly=True)
            processing_lease_expires_at = fields.Datetime(index=True, readonly=True)
            processing_token = fields.Char(index=True, readonly=True)
            delivered_at = fields.Datetime(readonly=True)
            dead_lettered_at = fields.Datetime(readonly=True)
            dispatch_lag_seconds = fields.Integer(required=True, default=0, readonly=True)
            last_error_code = fields.Char(readonly=True)

            _one_command_per_receipt = models.UniqueIndex("(receipt_id)")

            @api.model
            def _enqueue_receipt(self, receipt):
                registration = _ACTION_REGISTRY.get((receipt.aggregate_model, receipt.action))
                if not registration or registration["command_type"] != receipt.command_type:
                    raise ValidationError(self.env._("Receipt command is not registered."))
                values = {{
                    field_name: receipt.company_id.id
                    if field_name == "company_id"
                    else receipt[field_name]
                    for field_name in _AUTHORITY_FIELDS
                }}
                values.update(
                    {{
                        "receipt_id": receipt.id,
                        "aggregate_model": receipt.aggregate_model,
                        "aggregate_record_id": receipt.aggregate_record_id,
                        "aggregate_canonical_id": receipt.aggregate_canonical_id,
                        "expected_canonical_version": receipt.expected_canonical_version,
                        "aggregate_fingerprint": receipt.aggregate_fingerprint,
                        "action_contract_id": receipt.action_contract_id,
                        "command_type": receipt.command_type,
                        "payload": receipt.payload,
                        "idempotency_key": receipt.idempotency_key,
                        "semantic_fingerprint": receipt.semantic_fingerprint,
                        "correlation_id": receipt.correlation_id,
                        "causation_id": receipt.causation_id,
                    }}
                )
                return self.sudo().create(values)

            def _deliver_command(self):
                """Call the deployment adapter for the canonical FMOS command handler."""
                self.ensure_one()
                raise UserError(self.env._("No canonical FMOS command delivery adapter is installed."))

            def _retry_delay(self, attempts):
                return min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * (2 ** max(attempts - 1, 0)))

            def _lock_current_claim(self, processing_token):
                self.ensure_one()
                if not processing_token:
                    return False
                self.flush_recordset(["state", "processing_lease_expires_at", "processing_token"])
                self.env.cr.execute(
                    f"""
                    SELECT id FROM "{{self._table}}"
                    WHERE id = %s AND state = 'processing'
                      AND processing_token = %s AND processing_lease_expires_at > %s
                    FOR UPDATE
                    """,
                    (self.id, processing_token, fields.Datetime.now()),
                )
                return self.env.cr.fetchone() is not None

            def _process_one(self, processing_token=None):
                self.ensure_one()
                token = processing_token or self.processing_token
                if not self._lock_current_claim(token):
                    return False
                try:
                    with self.env.cr.savepoint():
                        self._deliver_command()
                except Exception as exc:
                    _logger.exception("Canonical FMOS command delivery failed for outbox row %s", self.id)
                    attempts = self.attempts + 1
                    values = {{
                        "attempts": attempts,
                        "last_attempt_at": fields.Datetime.now(),
                        "last_error_code": type(exc).__name__[:120],
                        "processing_started_at": False,
                        "processing_lease_expires_at": False,
                        "processing_token": False,
                    }}
                    if attempts >= self.max_attempts:
                        values.update(
                            {{
                                "state": "dead_letter",
                                "next_attempt_at": False,
                                "dead_lettered_at": fields.Datetime.now(),
                            }}
                        )
                    else:
                        values.update(
                            {{
                                "state": "retry",
                                "next_attempt_at": fields.Datetime.now()
                                + timedelta(seconds=self._retry_delay(attempts)),
                            }}
                        )
                    self.sudo().write(values)
                    return False
                delivered_at = fields.Datetime.now()
                self.sudo().write(
                    {{
                        "state": "done",
                        "attempts": self.attempts + 1,
                        "next_attempt_at": False,
                        "delivered_at": delivered_at,
                        "dispatch_lag_seconds": max(
                            0,
                            int((delivered_at - self.queued_at).total_seconds()),
                        ),
                        "last_error_code": False,
                        "processing_started_at": False,
                        "processing_lease_expires_at": False,
                        "processing_token": False,
                    }}
                )
                return True

            @api.model
            def _claim_due_commands(self):
                now = fields.Datetime.now()
                lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
                processing_token = str(uuid.uuid4())
                self.flush_model(
                    [
                        "state",
                        "next_attempt_at",
                        "processing_started_at",
                        "processing_lease_expires_at",
                        "processing_token",
                    ]
                )
                self.env.cr.execute(
                    f"""
                    WITH candidates AS (
                        SELECT id FROM "{{self._table}}"
                        WHERE (
                            state IN ('pending', 'retry') AND next_attempt_at <= %s
                        ) OR (
                            state = 'processing' AND processing_lease_expires_at <= %s
                        )
                        ORDER BY COALESCE(next_attempt_at, processing_lease_expires_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE "{{self._table}}" AS command
                    SET state = 'processing', next_attempt_at = NULL,
                        processing_started_at = %s, processing_lease_expires_at = %s,
                        processing_token = %s, last_attempt_at = %s,
                        write_date = %s, write_uid = %s
                    FROM candidates WHERE command.id = candidates.id
                    RETURNING command.id
                    """,
                    (
                        now,
                        now,
                        _CRON_BATCH_SIZE,
                        now,
                        lease_expires_at,
                        processing_token,
                        now,
                        now,
                        self.env.uid,
                    ),
                )
                rows = self.browse([row[0] for row in self.env.cr.fetchall()])
                rows.invalidate_recordset()
                return rows

            @api.model
            def _cron_process_due_commands(self):
                commands = self._claim_due_commands()
                remaining = len(commands)
                for command in commands:
                    command._process_one(command.processing_token)
                    remaining -= 1
                    if not self.env["ir.cron"]._commit_progress(1, remaining=remaining):
                        break

            def action_recover(self):
                group_xmlid = {f"{spec.module_name}.group_bridge_admin"!r}
                if {spec.role == "projection"!r}:
                    group_xmlid = {f"{spec.base_module}.group_bridge_admin"!r}
                if not self.env.user.has_group(group_xmlid):
                    raise UserError(self.env._("Bridge administrator access is required."))
                self.filtered(lambda row: row.state == "dead_letter").sudo().write(
                    {{
                        "state": "retry",
                        "attempts": 0,
                        "next_attempt_at": fields.Datetime.now(),
                        "last_error_code": False,
                        "dead_lettered_at": False,
                        "processing_started_at": False,
                        "processing_lease_expires_at": False,
                        "processing_token": False,
                    }}
                )
                return True
        '''
    )


def _render_projections(spec: ThinBridgeSpec) -> str:
    models = _protocol_models(spec)
    chunks = [
        '"""Generated FMOS-authoritative aggregate projections and fixed action methods."""',
        "",
        "from odoo import fields, models",
        "",
    ]
    for concept in spec.concepts:
        fields_block = "\n".join(_field_line(field) for field in concept.fields)
        actions_block = "\n\n".join(
            textwrap.indent(_action_method(action), "    ") for action in concept.actions
        )
        projection_fields = tuple(field.name for field in concept.fields)
        class_name = f"{_class_name(concept.name)}Projection"
        chunk = f'''
class {class_name}(models.Model):
    _name = {concept.model_name!r}
    _description = {concept.title!r}
    _inherit = [{models["mixin"]!r}, 'mail.thread', 'mail.activity.mixin']
    _order = "last_projection_at desc, id desc"

{fields_block}

    _canonical_identity = models.UniqueIndex(
        "(company_id, network_id, business_id, app_id, channel_id, "
        "identity_id, actor_id, install_id, canonical_id)"
    )

    @classmethod
    def _projection_fields(cls):
        return {projection_fields!r}
'''
        if actions_block:
            chunk += "\n" + actions_block + "\n"
        chunks.append(textwrap.dedent(chunk).strip())
        chunks.append("")
    return "\n".join(chunks).rstrip() + "\n"


def _render_groups(spec: ThinBridgeSpec) -> str:
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="module_category_fmos_bridge" model="ir.module.category">
                <field name="name">FMOS Bridge</field>
                <field name="sequence">90</field>
            </record>
            <record id="privilege_bridge" model="res.groups.privilege">
                <field name="name">{spec.module_title}</field>
                <field name="category_id" ref="module_category_fmos_bridge"/>
            </record>
            <record id="group_bridge_operator" model="res.groups">
                <field name="name">{spec.module_title} Operator</field>
                <field name="privilege_id" ref="privilege_bridge"/>
            </record>
            <record id="group_bridge_admin" model="res.groups">
                <field name="name">{spec.module_title} Administrator</field>
                <field name="privilege_id" ref="privilege_bridge"/>
                <field name="implied_ids" eval="[Command.link(ref('group_bridge_operator'))]"/>
            </record>
        </odoo>
        '''
    )


def _group_xmlids(spec: ThinBridgeSpec) -> tuple[str, str]:
    module = spec.base_module if spec.role == "projection" else spec.module_name
    return (f"{module}.group_bridge_operator", f"{module}.group_bridge_admin")


def _render_access(spec: ThinBridgeSpec) -> str:
    operator, admin = _group_xmlids(spec)
    model_names = [concept.model_name for concept in spec.concepts]
    if spec.role != "projection":
        protocol = _protocol_models(spec)
        model_names.extend(
            (
                protocol["crosswalk"],
                protocol["projection_receipt"],
                protocol["action_receipt"],
                protocol["outbox"],
            )
        )
    rows = ["id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink"]
    for model_name in model_names:
        slug = model_name.replace(".", "_")
        model_ref = _model_ref(model_name)
        rows.append(f"access_{slug}_operator,{slug} operator,{model_ref},{operator},1,0,0,0")
        rows.append(f"access_{slug}_admin,{slug} admin,{model_ref},{admin},1,0,0,0")
    return "\n".join(rows) + "\n"


def _render_rules(spec: ThinBridgeSpec) -> str:
    operator, _ = _group_xmlids(spec)
    model_names = [concept.model_name for concept in spec.concepts]
    if spec.role != "projection":
        protocol = _protocol_models(spec)
        model_names.extend(
            (
                protocol["crosswalk"],
                protocol["projection_receipt"],
                protocol["action_receipt"],
                protocol["outbox"],
            )
        )
    records = []
    for model_name in model_names:
        slug = model_name.replace(".", "_")
        records.append(
            f'''
            <record id="rule_{slug}_company" model="ir.rule">
                <field name="name">{model_name} active company isolation</field>
                <field name="model_id" ref="{_model_ref(model_name)}"/>
                <field name="domain_force">[('company_id', 'in', company_ids)]</field>
                <field name="groups" eval="[(4, ref('{operator}'))]"/>
                <field name="perm_read" eval="True"/>
                <field name="perm_write" eval="True"/>
                <field name="perm_create" eval="True"/>
                <field name="perm_unlink" eval="True"/>
            </record>
            '''
        )
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
        {"".join(records)}
        </odoo>
        '''
    )


def _render_cron(spec: ThinBridgeSpec) -> str:
    outbox = _protocol_models(spec)["outbox"]
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo noupdate="1">
            <record id="ir_cron_process_fmos_commands" model="ir.cron">
                <field name="name">{spec.module_title}: Process FMOS commands</field>
                <field name="model_id" ref="{_model_ref(outbox)}"/>
                <field name="state">code</field>
                <field name="code">model._cron_process_due_commands()</field>
                <field name="interval_number">1</field>
                <field name="interval_type">minutes</field>
                <field name="active" eval="True"/>
            </record>
        </odoo>
        '''
    )


def _render_protocol_views(spec: ThinBridgeSpec) -> str:
    protocol = _protocol_models(spec)
    views = []
    for key in ("crosswalk", "projection_receipt", "action_receipt", "outbox"):
        model_name = protocol[key]
        views.append(
            f'''
            <record id="view_{key}_list" model="ir.ui.view">
                <field name="name">{model_name}.list</field>
                <field name="model">{model_name}</field>
                <field name="arch" type="xml">
                    <list create="false" edit="false" delete="false">
                        <field name="company_id"/>
                        <field name="network_id"/>
                        <field name="app_id"/>
                        <field name="install_id"/>
                        <field name="aggregate_model"/>
                        <field name="canonical_id" optional="show"/>
                        <field name="aggregate_canonical_id" optional="show"/>
                        <field name="state" optional="show"/>
                    </list>
                </field>
            </record>
            <record id="action_{key}" model="ir.actions.act_window">
                <field name="name">{key.replace("_", " ").title()}</field>
                <field name="res_model">{model_name}</field>
                <field name="view_mode">list</field>
            </record>
            '''
        )
    # Remove fields that do not exist on every individual model.
    rendered = "".join(views)
    rendered = rendered.replace(
        '<field name="aggregate_canonical_id" optional="show"/>\n                        '
        '<field name="state" optional="show"/>',
        '<field name="aggregate_canonical_id" optional="hide"/>\n                        '
        '<field name="state" optional="hide"/>',
    )
    # Odoo validates even optional unknown fields, so use model-specific compact lists.
    model_fields = {
        "crosswalk": ("company_id", "network_id", "app_id", "install_id", "aggregate_model", "canonical_id"),
        "projection_receipt": (
            "company_id",
            "network_id",
            "app_id",
            "install_id",
            "aggregate_model",
            "canonical_id",
            "canonical_version",
        ),
        "action_receipt": (
            "company_id",
            "network_id",
            "app_id",
            "install_id",
            "aggregate_model",
            "aggregate_canonical_id",
            "action",
        ),
        "outbox": (
            "company_id",
            "network_id",
            "app_id",
            "install_id",
            "aggregate_model",
            "aggregate_canonical_id",
            "state",
        ),
    }
    views = []
    for key, field_names in model_fields.items():
        model_name = protocol[key]
        field_xml = "\n".join(f'                        <field name="{name}"/>' for name in field_names)
        views.append(
            f'''
            <record id="view_{key}_list" model="ir.ui.view">
                <field name="name">{model_name}.list</field>
                <field name="model">{model_name}</field>
                <field name="arch" type="xml">
                    <list create="false" edit="false" delete="false">
{field_xml}
                    </list>
                </field>
            </record>
            <record id="action_{key}" model="ir.actions.act_window">
                <field name="name">{key.replace("_", " ").title()}</field>
                <field name="res_model">{model_name}</field>
                <field name="view_mode">list</field>
            </record>
            '''
        )
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
        {"".join(views)}
        </odoo>
        '''
    )


def _render_projection_views(spec: ThinBridgeSpec) -> str:
    views = []
    for concept in spec.concepts:
        field_names = [
            "company_id",
            "canonical_id",
            "canonical_version",
            *(field.name for field in concept.fields[:4]),
            "reconciliation_state",
        ]
        list_fields = "\n".join(f'                        <field name="{name}"/>' for name in field_names)
        all_fields = [
            "company_id",
            "network_id",
            "business_id",
            "app_id",
            "channel_id",
            "identity_id",
            "actor_id",
            "install_id",
            "canonical_id",
            "canonical_version",
            "fingerprint",
            *(field.name for field in concept.fields),
            "origin",
            "correlation_id",
            "causation_id",
            "reconciliation_state",
            "last_projection_at",
        ]
        form_fields = "\n".join(f'                                <field name="{name}"/>' for name in all_fields)
        views.append(
            f'''
            <record id="view_{concept.name}_list" model="ir.ui.view">
                <field name="name">{concept.model_name}.list</field>
                <field name="model">{concept.model_name}</field>
                <field name="arch" type="xml">
                    <list create="false" edit="false" delete="false">
{list_fields}
                    </list>
                </field>
            </record>
            <record id="view_{concept.name}_form" model="ir.ui.view">
                <field name="name">{concept.model_name}.form</field>
                <field name="model">{concept.model_name}</field>
                <field name="arch" type="xml">
                    <form create="false" edit="false" delete="false">
                        <sheet>
                            <group>
{form_fields}
                            </group>
                        </sheet>
                        <chatter/>
                    </form>
                </field>
            </record>
            <record id="action_{concept.name}" model="ir.actions.act_window">
                <field name="name">{concept.title}</field>
                <field name="res_model">{concept.model_name}</field>
                <field name="view_mode">list,form</field>
            </record>
            '''
        )
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
        {"".join(views)}
        </odoo>
        '''
    )


def _render_menu(spec: ThinBridgeSpec) -> str:
    group = _group_xmlids(spec)[0]
    root_id = "menu_bridge_root"
    if spec.role == "projection":
        root = (
            f'<menuitem id="{root_id}" name="{spec.module_title}" sequence="90" '
            f'groups="{group}"/>'
        )
    else:
        root = (
            f'<menuitem id="{root_id}" name="{spec.module_title}" sequence="90" '
            f'groups="{group}"/>'
        )
    menus = []
    for sequence, concept in enumerate(spec.concepts, start=10):
        menus.append(
            f'<menuitem id="menu_{concept.name}" name="{concept.title}" '
            f'parent="{root_id}" action="action_{concept.name}" sequence="{sequence}"/>'
        )
    if spec.role != "projection":
        for sequence, key in enumerate(
            ("crosswalk", "projection_receipt", "action_receipt", "outbox"),
            start=70,
        ):
            menus.append(
                f'<menuitem id="menu_{key}" name="{key.replace("_", " ").title()}" '
                f'parent="{root_id}" action="action_{key}" sequence="{sequence}"/>'
            )
    return _clean(
        f'''
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            {root}
            {"".join(menus)}
        </odoo>
        '''
    )


def _proof_value(field: BridgeFieldSpec) -> object:
    if field.field_type == "Boolean":
        return True
    if field.field_type in {"Integer", "Float"}:
        return 7
    if field.field_type == "Json":
        return {"kind": "proof"}
    if field.field_type == "Date":
        return "2026-07-23"
    if field.field_type == "Datetime":
        return "2026-07-23 12:00:00"
    return "proof"


def _render_protocol_tests(spec: ThinBridgeSpec) -> str:
    protocol = _protocol_models(spec)
    registration = spec.protocol_actions[0] if spec.protocol_actions else None
    registration_assert = (
        f"self.assertIn(({registration.aggregate_model!r}, {registration.name!r}), protocol._ACTION_REGISTRY)"
        if registration
        else "self.assertEqual(protocol._ACTION_REGISTRY, {})"
    )
    return _clean(
        f'''
        """Generated install tests for shared protocol invariants."""

        from datetime import timedelta

        from odoo import fields
        from odoo.tests import TransactionCase, tagged

        from ..models import protocol


        @tagged("standard", "at_install")
        class Test{_class_name(spec.protocol_model_prefix)}Protocol(TransactionCase):
            def test_protocol_registry_is_fixed(self):
                {registration_assert}

            def test_duplicate_action_delivery_is_idempotent(self):
                self.assertIn("company_id", self.env[{protocol["action_receipt"]!r}]._fields)
                self.assertIn("receipt_id", self.env[{protocol["outbox"]!r}]._fields)

            def test_action_fingerprint_conflict_fails_closed(self):
                self.assertIn("semantic_fingerprint", self.env[{protocol["action_receipt"]!r}]._fields)

            def test_timeout_recovery_fences_stale_worker(self):
                outbox = self.env[{protocol["outbox"]!r}]
                self.assertTrue(callable(outbox._claim_due_commands))
                self.assertTrue(callable(outbox._lock_current_claim))
                self.assertGreater(fields.Datetime.now(), fields.Datetime.now() - timedelta(seconds=901))

            def test_two_company_isolation(self):
                other = self.env["res.company"].create({{"name": "Protocol Isolation Proof"}})
                scoped = self.env[{protocol["outbox"]!r}].with_context(
                    allowed_company_ids=[self.env.company.id]
                )
                self.assertNotIn(other.id, scoped.env.companies.ids)
                rule = scoped.env["ir.rule"].search(
                    [("model_id.model", "=", {protocol["outbox"]!r})],
                    limit=1,
                )
                self.assertIn("company_ids", rule.domain_force)
        '''
    )


def _render_projection_test_case(spec: ThinBridgeSpec, concept: ThinConceptSpec) -> str:
    first = concept.fields[0]
    values = {
        field.name: _proof_value(field)
        for field in concept.fields
        if field.required or field == first
    }
    action = concept.actions[0] if concept.actions else None
    action_proof = "        # This aggregate is intentionally projection-only."
    if action:
        payload = {name: "proof" for name in action.payload_fields}
        action_proof = textwrap.indent(
            textwrap.dedent(
                f'''
                receipt = record.action_{action.name}(
                    idempotency_key="action-proof-0001",
                    correlation_id="action-correlation-0001",
                    expected_canonical_version=record.canonical_version,
                    payload={payload!r},
                )
                duplicate = record.action_{action.name}(
                    idempotency_key="action-proof-0001",
                    correlation_id="action-correlation-0001",
                    expected_canonical_version=record.canonical_version,
                    payload={payload!r},
                )
                self.assertEqual(receipt, duplicate)
                self.assertEqual(self.Outbox.search_count([("receipt_id", "=", receipt.id)]), 1)
                with self.assertRaises(ValidationError):
                    record.action_{action.name}(
                        idempotency_key="action-proof-0001",
                        correlation_id="action-correlation-0001",
                        expected_canonical_version=record.canonical_version,
                        payload={{**{payload!r}, "unexpected_field": "conflict"}},
                    )
                with self.assertRaises(ValidationError):
                    record.action_{action.name}(
                        idempotency_key="action-version-proof-0001",
                        correlation_id="action-version-correlation-0001",
                        expected_canonical_version=record.canonical_version + 1,
                        payload={payload!r},
                    )
                '''
            ).strip(),
            "        ",
        )
    rendered = _clean(
        f'''
        """Generated Odoo transaction tests for both bridge directions."""

        from odoo.exceptions import AccessError, ValidationError
        from odoo.tests import TransactionCase, tagged


        @tagged("standard", "at_install")
        class Test{_class_name(concept.name)}Projection(TransactionCase):
            @classmethod
            def setUpClass(cls):
                super().setUpClass()
                group = cls.env.ref({_group_xmlids(spec)[1]!r})
                user = cls.env["res.users"].with_context(no_reset_password=True).create(
                    {{
                        "name": "Bridge Projection Test",
                        "login": "bridge-projection-test@example.test",
                        "company_id": cls.env.company.id,
                        "company_ids": [(6, 0, [cls.env.company.id])],
                        "group_ids": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
                    }}
                )
                cls.Model = cls.env[{concept.model_name!r}].with_user(user)
                cls.Outbox = cls.env[{_protocol_models(spec)["outbox"]!r}].with_user(user)
                cls.authority = {{
                    "network_id": "network-proof-0001",
                    "business_id": "business-proof-0001",
                    "app_id": "application-proof-0001",
                    "channel_id": "channel-proof-0001",
                    "identity_id": "identity-proof-0001",
                    "actor_id": "actor-proof-0001",
                    "company_id": cls.env.company.id,
                    "install_id": "install-proof-0001",
                }}
                cls.values = {values!r}

            def _project(self, version=1, values=None, authority=None, origin="fmos", causation_id=None):
                authority = authority or self.authority
                values = values or self.values
                fingerprint = self.Model._projection_fingerprint(
                    authority,
                    "canonical-proof-0001",
                    version,
                    values,
                )
                return self.Model.apply_fmos_projection(
                    authority_scope=authority,
                    canonical_id="canonical-proof-0001",
                    canonical_version=version,
                    fingerprint=fingerprint,
                    values=values,
                    origin=origin,
                    correlation_id=f"correlation-proof-{{version:04d}}",
                    causation_id=causation_id,
                )

            def test_projection_both_directions_and_loop_prevention(self):
                record = self._project(causation_id="action-causation-0001")
                self.assertEqual(record.origin, "fmos")
                self.assertEqual(self.Outbox.search_count([]), 0)
        __ACTION_PROOF__
                with self.assertRaises(ValidationError):
                    self._project(version=2, origin="odoo")

            def test_duplicate_projection_delivery(self):
                first = self._project()
                duplicate = self._project()
                self.assertEqual(first, duplicate)

            def test_projection_fingerprint_conflict(self):
                self._project()
                with self.assertRaises(ValidationError):
                    self.Model.apply_fmos_projection(
                        authority_scope=self.authority,
                        canonical_id="canonical-proof-0001",
                        canonical_version=1,
                        fingerprint="0" * 64,
                        values=self.values,
                        origin="fmos",
                        correlation_id="correlation-conflict-0001",
                    )

            def test_projection_version_conflict(self):
                self._project(version=2)
                with self.assertRaises(ValidationError):
                    self._project(version=1)

            def test_two_company_isolation(self):
                other = self.env["res.company"].create({{"name": "Projection Isolation Proof"}})
                authority = dict(self.authority, company_id=other.id)
                with self.assertRaises(AccessError):
                    self._project(authority=authority)
        '''
    )
    return rendered.replace("__ACTION_PROOF__", action_proof)


def _render_projection_tests(spec: ThinBridgeSpec) -> str:
    return "\n\n".join(
        _render_projection_test_case(spec, concept)
        for concept in spec.concepts
    )


def _render_install_contract(spec: ThinBridgeSpec) -> str:
    protocol = _protocol_models(spec)
    concept_models = [concept.model_name for concept in spec.concepts]
    owned_protocol_models = (
        [
            protocol["crosswalk"],
            protocol["projection_receipt"],
            protocol["action_receipt"],
            protocol["outbox"],
        ]
        if spec.role != "projection"
        else []
    )
    allowlisted_actions = {
        action.name: action.command_type
        for concept in spec.concepts
        for action in concept.actions
    }
    if spec.role == "protocol_base":
        allowlisted_actions = {
            action.name: action.command_type
            for action in spec.protocol_actions
        }
    aggregate_model = concept_models[0] if concept_models else protocol["crosswalk"]
    payload = {
        "schema_version": "1.0",
        "odoo_version": "19.0",
        "module": spec.module_name,
        "bridge_mode": "fmos_authoritative",
        "authoritative_writer": "fmos",
        "aggregate_model": aggregate_model,
        "models": sorted({*concept_models, *owned_protocol_models}),
        "dependencies": _dependencies(spec),
        "contracts": {
            "provider": spec.provider_contract_id,
            "wrapper": spec.wrapper_contract_id,
            "projection": spec.projection_contract_id,
            "action": spec.action_contract_id,
            "return_authorization": "fmos.bidirectional.return-authorization.v1",
        },
        "receipt_model": protocol["action_receipt"],
        "outbox_model": protocol["outbox"],
        "authority_fields": list(AUTHORITY_FIELDS),
        "authority_semantics": {
            "required": True,
            "fallbacks": False,
            "company_isolation": "active company record rules on projections, crosswalks, receipts, and outbox",
            "canonical_identity": "all authority fields plus aggregate model and canonical ID",
            "cross_scope_reassignment": "prohibited",
        },
        "projection_fields": {
            concept.model_name: [field.name for field in concept.fields]
            for concept in spec.concepts
        },
        "projection_semantics": {
            "idempotency": "immutable version receipt",
            "version": "monotonic; same version requires identical canonical fingerprint",
            "concurrency": "transaction-scoped PostgreSQL advisory lock",
            "origin_allowlist": ["fmos", "reconciler"],
            "loop_prevention": "projections never enqueue actions; odoo-origin projections are rejected",
        },
        "allowlisted_actions": allowlisted_actions,
        "action_semantics": {
            "payload_fields": {
                concept.model_name: {
                    action.name: list(action.payload_fields)
                    for action in concept.actions
                }
                for concept in spec.concepts
            },
            "idempotency_identity": ["company_id", "idempotency_key"],
            "fingerprint": "immutable semantic SHA-256 over scope, aggregate version, action, and payload",
            "canonical_handler": "command_type selects a compile-time allowlisted FMOS command handler",
            "dynamic_model_or_method_dispatch": False,
            "data_policy": {
                "pii": False,
                "secrets": False,
                "operator_data": False,
                "free_form_payloads": False,
            },
        },
        "retry_dead_letter_recovery": {
            "max_attempts": spec.max_attempts,
            "retry_base_seconds": spec.retry_base_seconds,
            "retry_cap_seconds": spec.retry_cap_seconds,
            "claim": "FOR UPDATE SKIP LOCKED",
            "processing_lease_seconds": 900,
            "timeout_recovery": "expired leases are atomically reclaimed with a fresh fencing token",
            "dead_letter": True,
            "administrator_recovery": True,
        },
        "reconciliation": {
            "observation_api": "apply_reconciliation_observation",
            "telemetry": [
                "reconciliation_state",
                "source_version",
                "destination_version",
                "source_fingerprint",
                "destination_fingerprint",
                "reconciliation_lag",
                "reconciliation_retry_count",
                "reconciliation_dead_letter_count",
            ],
        },
        "telemetry_fields": [
            "correlation_id",
            "causation_id",
            "queued_at",
            "last_attempt_at",
            "processing_started_at",
            "processing_lease_expires_at",
            "processing_token",
            "delivered_at",
            "dead_lettered_at",
            "dispatch_lag_seconds",
            "attempts",
            "last_error_code",
        ],
        "telemetry_pii": False,
        "delivery_adapter": {
            "status": "required",
            "seam": "deployment addon overrides fixed _deliver_command",
            "runtime_dispatch": False,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _render_readme(spec: ThinBridgeSpec) -> str:
    model_lines = "\n".join(f"* ``{concept.model_name}``" for concept in spec.concepts) or "* protocol only"
    return (
        f"{spec.module_title}\n"
        f"{'=' * len(spec.module_title)}\n\n"
        "Factory-generated Odoo 19 thin wrapper. FMOS remains the sole business writer.\n\n"
        f"{model_lines}\n\n"
        "The addon stores company-scoped projections, immutable receipts, crosswalks,\n"
        "command outbox telemetry, and chatter audit events. It contains no PII,\n"
        "credentials, operator notes, WordPress coupling, or route-local sync logic.\n"
    )


def _render_pot(spec: ThinBridgeSpec) -> str:
    labels = {spec.module_title}
    for concept in spec.concepts:
        labels.add(concept.title)
        labels.update(field.label for field in concept.fields)
        labels.update(action.label for action in concept.actions)
    entries = "\n\n".join(f'msgid {label!r}\nmsgstr ""' for label in sorted(labels))
    return _clean(
        f'''
        # Translation template for {spec.module_name}.
        msgid ""
        msgstr ""
        "Project-Id-Version: {spec.version}\\n"
        "Content-Type: text/plain; charset=UTF-8\\n"

        {entries}
        '''
    )


def _render_migration(stage: str, spec: ThinBridgeSpec) -> str:
    return _clean(
        f'''
        """{stage.title()} migration for generated {spec.module_name}."""

        import logging

        _logger = logging.getLogger(__name__)


        def migrate(cr, version):
            if version:
                _logger.info("{spec.module_name} {stage} migration from %s", version)
        '''
    )


def render_thin_files(spec: ThinBridgeSpec) -> dict[str, str]:
    """Render one complete schema-v2 addon."""
    files = {
        "INSTALL_CONTRACT.json": _render_install_contract(spec),
        "README.rst": _render_readme(spec),
        "__init__.py": _render_root_init(),
        "__manifest__.py": _render_manifest(spec),
        f"i18n/{spec.module_name}.pot": _render_pot(spec),
        f"migrations/{spec.version}/pre-migration.py": _render_migration("pre", spec),
        f"migrations/{spec.version}/post-migration.py": _render_migration("post", spec),
        "models/__init__.py": _render_models_init(spec),
        "security/ir.model.access.csv": _render_access(spec),
        "security/record_rules.xml": _render_rules(spec),
        "tests/__init__.py": (
            "from . import test_protocol\n"
            if spec.role == "protocol_base"
            else "from . import test_projections\n"
            if spec.role == "projection"
            else "from . import test_protocol\nfrom . import test_projections\n"
        ),
        "views/menu.xml": _render_menu(spec),
    }
    if spec.role != "projection":
        files.update(
            {
                "data/cron.xml": _render_cron(spec),
                "models/protocol.py": _render_protocol(spec),
                "security/groups.xml": _render_groups(spec),
                "tests/test_protocol.py": _render_protocol_tests(spec),
                "views/protocol_views.xml": _render_protocol_views(spec),
            }
        )
    if spec.concepts:
        files.update(
            {
                "models/projections.py": _render_projections(spec),
                "tests/test_projections.py": _render_projection_tests(spec),
                "views/projection_views.xml": _render_projection_views(spec),
            }
        )
    return files
