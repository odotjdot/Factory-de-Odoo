"""Deterministic text renderers for the Odoo 19 FMOS bridge addon."""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Iterable

from amil_utils.odoo_bridge.schema import ODOO_NATIVE_TARGETS, BridgeActionSpec, BridgeFieldSpec, BridgeSpec

_AUTHORITY_FIELDS = (
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
    return textwrap.dedent(value).lstrip().rstrip() + "\n"


def _class_name(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", value) if part)


def _xml_id(model_name: str) -> str:
    return f"model_{model_name.replace('.', '_')}"


def _py_tuple(values: Iterable[str]) -> str:
    items = tuple(values)
    if len(items) == 1:
        return f"({items[0]!r},)"
    return repr(items)


def _field_line(field: BridgeFieldSpec) -> str:
    options = [f"string={field.label!r}"]
    if field.required:
        options.append("required=True")
    if field.index:
        options.append("index=True")
    return f"    {field.name} = fields.{field.field_type}({', '.join(options)})"


def _dependencies(spec: BridgeSpec) -> tuple[str, ...]:
    dependencies = ["base"]
    if spec.bridge_mode == "odoo_native_extension":
        dependency = ODOO_NATIVE_TARGETS[spec.model_name]
        if dependency not in dependencies:
            dependencies.append(dependency)
    return tuple(dependencies)


def _queue_model(spec: BridgeSpec) -> str:
    return f"fm.{spec.domain}.command.outbox"


def _receipt_model(spec: BridgeSpec) -> str:
    return f"fm.{spec.domain}.action.receipt"


def _render_manifest(spec: BridgeSpec) -> str:
    data_files = (
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/cron.xml",
        "views/bridge_record_views.xml",
        "views/action_receipt_views.xml",
        "views/command_outbox_views.xml",
        "views/menu.xml",
    )
    return _clean(
        f"""
        {{
            "name": {spec.module_title!r},
            "summary": "Company-isolated bidirectional FMOS/Odoo bridge",
            "version": {spec.version!r},
            "author": {spec.author!r},
            "license": {spec.license!r},
            "category": "Technical",
            "depends": {list(_dependencies(spec))!r},
            "data": {list(data_files)!r},
            "post_init_hook": "post_init_hook",
            "installable": True,
            "application": False,
        }}
        """
    )


def _render_root_init() -> str:
    return "from . import models\nfrom .hooks import post_init_hook\n"


def _render_models_init(spec: BridgeSpec) -> str:
    return f"from . import {spec.domain}_bridge\nfrom . import action_receipt\nfrom . import command_outbox\n"


def _render_hooks(spec: BridgeSpec) -> str:
    required_models = (spec.model_name, _receipt_model(spec), _queue_model(spec))
    required_fields = (
        (
            *_AUTHORITY_FIELDS,
            "canonical_id",
            "canonical_version",
            "fingerprint",
            "origin",
            "correlation_id",
            "causation_id",
            "last_projection_at",
            "reconciliation_state",
            "source_version",
            "destination_version",
            "source_fingerprint",
            "destination_fingerprint",
            "last_reconciled_at",
            "reconciliation_lag",
        )
        if spec.bridge_mode == "fmos_authoritative"
        else ()
    )
    field_verification = (
        textwrap.indent(
            _clean(
                f"""
                missing_fields = [
                    field_name
                    for field_name in _REQUIRED_FIELDS
                    if field_name not in env[{spec.model_name!r}]._fields
                ]
                if missing_fields:
                    raise UserError(
                        env._(
                            "Bridge installation is incomplete; missing fields: %(fields)s",
                            fields=", ".join(missing_fields),
                        )
                    )
                """
            ).rstrip(),
            "    ",
        )
        if required_fields
        else ""
    )
    template = _clean(
        f'''
        """Install-time contract verification for {spec.module_name}."""

        from odoo.exceptions import UserError

        _REQUIRED_MODELS = {_py_tuple(required_models)}
        _REQUIRED_FIELDS = {_py_tuple(required_fields)}


        def post_init_hook(env):
            """Fail installation if the generated bridge contract is incomplete."""
            missing_models = [model for model in _REQUIRED_MODELS if model not in env]
            if missing_models:
                raise UserError(
                    env._(
                        "Bridge installation is incomplete; missing models: %(models)s",
                        models=", ".join(missing_models),
                    )
                )
        __FIELD_VERIFICATION__
        '''
    )
    return template.replace("__FIELD_VERIFICATION__", field_verification)


def _render_native_bridge_model(spec: BridgeSpec) -> str:
    """Render an Odoo-authoritative extension with fixed outbound capture methods."""
    class_name = f"{_class_name(spec.domain)}NativeBridge"
    methods = "\n\n".join(
        textwrap.indent(
            _clean(
                f'''
                def action_{action.name}(
                    self,
                    *,
                    authority_scope,
                    idempotency_key,
                    correlation_id,
                    payload=None,
                ):
                    """Snapshot this Odoo-owned record and enqueue a fixed FMOS event."""
                    return self._capture_fmos_event(
                        {action.name!r},
                        {action.command_type!r},
                        authority_scope,
                        idempotency_key,
                        correlation_id,
                        payload or {{}},
                    )
                '''
            ).rstrip(),
            "    ",
        )
        for action in spec.actions
    )
    template = _clean(
        f'''
        """Odoo-authoritative extension exposing fixed Odoo-to-FMOS event methods."""

        from odoo import models
        from odoo.exceptions import AccessError, ValidationError

        _ALLOWED_ACTIONS = frozenset({_py_tuple(tuple(action.name for action in spec.actions))})


        class {class_name}(models.Model):
            _inherit = {spec.model_name!r}

            def _capture_fmos_event(
                self,
                action,
                command_type,
                authority_scope,
                idempotency_key,
                correlation_id,
                payload,
            ):
                self.ensure_one()
                if not self.env.user.has_group({f"{spec.module_name}.group_bridge_operator"!r}):
                    raise AccessError(self.env._("Bridge operator access is required."))
                if action not in _ALLOWED_ACTIONS:
                    raise ValidationError(self.env._("The requested bridge action is not allowlisted."))
                return self.env[{_receipt_model(spec)!r}].sudo()._capture_native_action(
                    source=self,
                    action=action,
                    command_type=command_type,
                    authority_scope=authority_scope,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                    payload=payload,
                )

        __NATIVE_ACTIONS__
        '''
    )
    return template.replace("__NATIVE_ACTIONS__", methods)


def _render_bridge_model(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_bridge_model(spec)

    class_name = f"{_class_name(spec.domain)}Bridge"
    identity = f"    _name = {spec.model_name!r}\n    _description = {spec.module_title!r}\n"
    data_fields = "\n".join(_field_line(field) for field in spec.fields)
    projection_names = tuple(field.name for field in spec.fields)
    action_names = tuple(action.name for action in spec.actions)
    generated_actions = "\n\n".join(_render_action_method(spec, action) for action in spec.actions)
    origin_options = "(('fmos', 'FMOS'), ('odoo', 'Odoo'), ('reconciler', 'Reconciler'))"
    template = _clean(
        f'''
        """Canonical FMOS projection and fixed Odoo action surface."""

        import hashlib
        import json
        import re

        from odoo import api, fields, models
        from odoo.exceptions import AccessError, ValidationError

        _ALLOWED_PROJECTION_FIELDS = frozenset({_py_tuple(projection_names)})
        _ALLOWED_ACTIONS = frozenset({_py_tuple(action_names + ("reconcile",))})
        _AUTHORITY_FIELDS = frozenset({_py_tuple(_AUTHORITY_FIELDS)})
        _IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{{7,127}}\\Z")
        _FINGERPRINT_RE = re.compile(r"[0-9a-f]{{64}}\\Z")


        class {class_name}(models.Model):
        __BRIDGE_IDENTITY__
            _check_company_auto = True

            company_id = fields.Many2one(
                "res.company",
                string="Company",
                required=True,
                default=lambda self: self.env.company,
                index=True,
            )
            network_id = fields.Char(string="Network ID", required=True, index=True, copy=False)
            business_id = fields.Char(string="Business ID", required=True, index=True, copy=False)
            app_id = fields.Char(string="App ID", required=True, index=True, copy=False)
            channel_id = fields.Char(string="Channel ID", required=True, index=True, copy=False)
            identity_id = fields.Char(string="Identity ID", required=True, index=True, copy=False)
            actor_id = fields.Char(string="Actor ID", required=True, index=True, copy=False)
            install_id = fields.Char(string="Install ID", required=True, index=True, copy=False)
            canonical_id = fields.Char(string="FMOS Canonical ID", required=True, index=True, copy=False)
            canonical_version = fields.Integer(string="Canonical Version", required=True, default=1, copy=False)
            fingerprint = fields.Char(string="Payload Fingerprint", required=True, copy=False)
            origin = fields.Selection({origin_options}, string="Change Origin", required=True, copy=False)
            correlation_id = fields.Char(string="Correlation ID", required=True, index=True, copy=False)
            causation_id = fields.Char(string="Causation ID", index=True, copy=False)
            last_projection_at = fields.Datetime(string="Last Projection At", required=True, copy=False)
            reconciliation_state = fields.Selection(
                (
                    ("pending", "Pending"),
                    ("in_sync", "In Sync"),
                    ("diverged", "Diverged"),
                    ("error", "Error"),
                ),
                string="Reconciliation State",
                required=True,
                default="pending",
                index=True,
                copy=False,
            )
            source_version = fields.Integer(string="FMOS Source Version", required=True, default=0, copy=False)
            destination_version = fields.Integer(string="Odoo Destination Version", required=True, default=0, copy=False)
            source_fingerprint = fields.Char(string="FMOS Source Fingerprint", copy=False)
            destination_fingerprint = fields.Char(string="Odoo Destination Fingerprint", copy=False)
            last_reconciled_at = fields.Datetime(string="Last Reconciled At", copy=False)
            last_reconciliation_error_code = fields.Char(string="Last Reconciliation Error Code", copy=False)
            reconciliation_lag = fields.Integer(string="Reconciliation Version Lag", required=True, default=0, copy=False)
            reconciliation_retry_count = fields.Integer(
                string="Reconciliation Retry Count", required=True, default=0, copy=False
            )
            reconciliation_dead_letter_count = fields.Integer(
                string="Reconciliation Dead Letter Count", required=True, default=0, copy=False
            )
        __BRIDGE_DATA_FIELDS__

            _canonical_identity = models.UniqueIndex(
                "(company_id, network_id, business_id, app_id, channel_id, "
                "identity_id, actor_id, install_id, canonical_id)"
            )
            _positive_canonical_version = models.Constraint(
                "CHECK(canonical_version > 0)",
                "Canonical version must be positive.",
            )
            _valid_fingerprint = models.Constraint(
                "CHECK(char_length(fingerprint) = 64)",
                "Fingerprint must be a SHA-256 hexadecimal digest.",
            )

            @api.model
            def _require_bridge_operator(self):
                if not self.env.user.has_group({f"{spec.module_name}.group_bridge_operator"!r}):
                    raise AccessError(self.env._("Bridge operator access is required."))

            @api.model
            def _validate_identifier(self, value, field_label):
                if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                    raise ValidationError(
                        self.env._("%(field)s is not a valid bridge identifier.", field=field_label)
                    )
                return value

            @api.model
            def _validate_fingerprint(self, value, field_label):
                if not isinstance(value, str) or not _FINGERPRINT_RE.fullmatch(value):
                    raise ValidationError(
                        self.env._("%(field)s must be a lowercase SHA-256 digest.", field=field_label)
                    )
                return value

            @api.model
            def _validate_authority_scope(self, authority_scope):
                if not isinstance(authority_scope, dict) or set(authority_scope) != _AUTHORITY_FIELDS:
                    raise ValidationError(self.env._("Authority scope must contain every exact authority field."))
                company_id = authority_scope["company_id"]
                if not isinstance(company_id, int) or isinstance(company_id, bool):
                    raise ValidationError(self.env._("authority company_id must be an integer."))
                if company_id not in self.env.companies.ids:
                    raise AccessError(self.env._("The requested company is outside the active company scope."))
                normalized = {{"company_id": company_id}}
                for field_name in sorted(_AUTHORITY_FIELDS - {{"company_id"}}):
                    normalized[field_name] = self._validate_identifier(
                        authority_scope[field_name],
                        field_name,
                    )
                return normalized

            @api.model
            def _authority_domain(self, authority_scope):
                return [
                    (field_name, "=", authority_scope[field_name])
                    for field_name in {_py_tuple(_AUTHORITY_FIELDS)}
                ]

            @api.model
            def _projection_lock_keys(self, authority_scope, canonical_id):
                material = json.dumps(
                    {{
                        "authority_scope": authority_scope,
                        "canonical_id": canonical_id,
                    }},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
                digest = hashlib.sha256(material).digest()
                return (
                    int.from_bytes(digest[:4], byteorder="big", signed=True),
                    int.from_bytes(digest[4:8], byteorder="big", signed=True),
                )

            @api.model
            def _acquire_projection_lock(self, authority_scope, canonical_id):
                lock_keys = self._projection_lock_keys(authority_scope, canonical_id)
                self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", lock_keys)

            def write(self, values):
                immutable_fields = sorted(set(values) & _AUTHORITY_FIELDS)
                for record in self:
                    for field_name in immutable_fields:
                        current = record.company_id.id if field_name == "company_id" else record[field_name]
                        incoming = values[field_name]
                        incoming = incoming.id if field_name == "company_id" and hasattr(incoming, "id") else incoming
                        if current != incoming:
                            raise ValidationError(
                                self.env._("Authority scope is immutable; cross-scope reassignment is prohibited.")
                            )
                return super().write(values)

            @api.model
            def _projection_fingerprint(self, canonical_id, canonical_version, authority_scope, values):
                body = {{
                    "authority_scope": authority_scope,
                    "canonical_id": canonical_id,
                    "canonical_version": canonical_version,
                    "values": values,
                }}
                try:
                    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
                except (TypeError, ValueError) as exc:
                    raise ValidationError(self.env._("Projection values must be JSON serializable.")) from exc
                return hashlib.sha256(encoded).hexdigest()

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
                """Apply one monotonic, fingerprinted FMOS projection idempotently."""
                self._require_bridge_operator()
                authority = self._validate_authority_scope(authority_scope)
                company_id = authority["company_id"]
                self._validate_identifier(canonical_id, self.env._("Canonical ID"))
                self._validate_identifier(correlation_id, self.env._("Correlation ID"))
                if causation_id is not None:
                    self._validate_identifier(causation_id, self.env._("Causation ID"))
                if not isinstance(canonical_version, int) or isinstance(canonical_version, bool) or canonical_version < 1:
                    raise ValidationError(self.env._("canonical_version must be a positive integer."))
                if origin not in ("fmos", "reconciler"):
                    raise ValidationError(self.env._("Projection origin must be FMOS or reconciler."))
                if not isinstance(values, dict):
                    raise ValidationError(self.env._("Projection values must be an object."))
                unknown_fields = sorted(set(values) - _ALLOWED_PROJECTION_FIELDS)
                if unknown_fields:
                    raise ValidationError(
                        self.env._(
                            "Projection contains non-allowlisted fields: %(fields)s",
                            fields=", ".join(unknown_fields),
                        )
                    )
                expected_fingerprint = self._projection_fingerprint(
                    canonical_id,
                    canonical_version,
                    authority,
                    values,
                )
                if fingerprint != expected_fingerprint:
                    raise ValidationError(self.env._("Projection fingerprint does not match its canonical payload."))

                company = self.env["res.company"].browse(company_id).exists()
                if not company:
                    raise ValidationError(self.env._("Projection company does not exist."))
                self._acquire_projection_lock(authority, canonical_id)
                scoped = self.sudo().with_company(company)
                record = scoped.search(
                    self._authority_domain(authority) + [("canonical_id", "=", canonical_id)],
                    limit=1,
                )
                if record:
                    if canonical_version < record.canonical_version:
                        raise ValidationError(self.env._("Projection version is older than the stored version."))
                    if canonical_version == record.canonical_version:
                        if fingerprint != record.fingerprint:
                            raise ValidationError(
                                self.env._("Projection version conflicts with the stored fingerprint.")
                            )
                        return record

                bridge_values = dict(values)
                bridge_values.update(
                    {{
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
                        "last_reconciliation_error_code": False,
                        "reconciliation_lag": 0,
                    }}
                )
                if record:
                    record.write(bridge_values)
                    return record
                return scoped.create(dict(bridge_values, **authority))

            def _capture_allowlisted_action(
                self,
                action,
                command_type,
                idempotency_key,
                payload,
                correlation_id,
                expected_canonical_version,
            ):
                self.ensure_one()
                self._require_bridge_operator()
                if action not in _ALLOWED_ACTIONS:
                    raise ValidationError(self.env._("The requested bridge action is not allowlisted."))
                if expected_canonical_version != self.canonical_version:
                    raise ValidationError(self.env._("Expected canonical version does not match the aggregate."))
                return self.env[{_receipt_model(spec)!r}].sudo()._capture_action(
                    aggregate=self,
                    action=action,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                    correlation_id=correlation_id,
                    expected_canonical_version=expected_canonical_version,
                )

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
                """Persist one non-PII reconciliation observation."""
                self._require_bridge_operator()
                authority = self._validate_authority_scope(authority_scope)
                self._validate_identifier(canonical_id, self.env._("Canonical ID"))
                self._validate_identifier(correlation_id, self.env._("Correlation ID"))
                self._validate_fingerprint(source_fingerprint, self.env._("Source fingerprint"))
                self._validate_fingerprint(destination_fingerprint, self.env._("Destination fingerprint"))
                if (
                    not isinstance(source_version, int)
                    or isinstance(source_version, bool)
                    or source_version < 0
                    or not isinstance(destination_version, int)
                    or isinstance(destination_version, bool)
                    or destination_version < 0
                ):
                    raise ValidationError(self.env._("Reconciliation versions must be non-negative integers."))
                company = self.env["res.company"].browse(authority["company_id"]).exists()
                if not company:
                    raise ValidationError(self.env._("Reconciliation company does not exist."))
                self._acquire_projection_lock(authority, canonical_id)
                record = self.sudo().with_company(company).search(
                    self._authority_domain(authority) + [("canonical_id", "=", canonical_id)],
                    limit=1,
                )
                if not record:
                    raise ValidationError(self.env._("Reconciliation aggregate does not exist."))
                in_sync = (
                    source_version == destination_version
                    and source_fingerprint == destination_fingerprint
                )
                record.write(
                    {{
                        "reconciliation_state": "in_sync" if in_sync else "diverged",
                        "source_version": source_version,
                        "destination_version": destination_version,
                        "source_fingerprint": source_fingerprint,
                        "destination_fingerprint": destination_fingerprint,
                        "last_reconciled_at": fields.Datetime.now(),
                        "last_reconciliation_error_code": False,
                        "reconciliation_lag": abs(source_version - destination_version),
                        "correlation_id": correlation_id,
                    }}
                )
                return record

        __BRIDGE_ACTIONS__

            def action_request_reconciliation(self):
                """Enqueue a deterministic reconciliation command for this version."""
                self.ensure_one()
                self.sudo().write({{"reconciliation_state": "pending"}})
                idempotency_key = (
                    f"reconcile:{{self.canonical_id}}:{{self.canonical_version}}:{{self.fingerprint[:16]}}"
                )
                return self._capture_allowlisted_action(
                    "reconcile",
                    {f"{spec.domain}.reconcile.requested.v1"!r},
                    idempotency_key,
                    {{
                        "canonical_id": self.canonical_id,
                        "canonical_version": self.canonical_version,
                        "fingerprint": self.fingerprint,
                    }},
                    idempotency_key,
                    self.canonical_version,
                )
        '''
    )
    return (
        template.replace("__BRIDGE_IDENTITY__", identity.rstrip())
        .replace("__BRIDGE_DATA_FIELDS__", data_fields)
        .replace("__BRIDGE_ACTIONS__", generated_actions)
    )


def _render_action_method(spec: BridgeSpec, action: BridgeActionSpec) -> str:
    return textwrap.indent(
        _clean(
            f'''
            def action_{action.name}(
                self,
                *,
                idempotency_key,
                correlation_id,
                expected_canonical_version,
                payload=None,
            ):
                """Capture the allowlisted {action.label} command."""
                return self._capture_allowlisted_action(
                    {action.name!r},
                    {action.command_type!r},
                    idempotency_key,
                    payload or {{}},
                    correlation_id,
                    expected_canonical_version,
                )
            '''
        ).rstrip(),
        "    ",
    )


def _render_receipt_model(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_receipt_model(spec)

    class_name = f"{_class_name(spec.domain)}ActionReceipt"
    action_values = tuple(action.name for action in spec.actions) + ("reconcile",)
    action_selection = tuple((name, name.replace("_", " ").title()) for name in action_values)
    aggregate_field = f"{spec.domain}_id"
    return _clean(
        f'''
        """Immutable Odoo action receipts created atomically with command outbox rows."""

        import hashlib
        import json

        from odoo import api, fields, models
        from odoo.exceptions import ValidationError

        _ALLOWED_ACTIONS = frozenset({_py_tuple(action_values)})


        class {class_name}(models.Model):
            _name = {_receipt_model(spec)!r}
            _description = {f"{spec.module_title} Action Receipt"!r}
            _order = "create_date desc, id desc"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True)
            network_id = fields.Char(required=True, readonly=True, index=True)
            business_id = fields.Char(required=True, readonly=True, index=True)
            app_id = fields.Char(required=True, readonly=True, index=True)
            channel_id = fields.Char(required=True, readonly=True, index=True)
            identity_id = fields.Char(required=True, readonly=True, index=True)
            actor_id = fields.Char(required=True, readonly=True, index=True)
            install_id = fields.Char(required=True, readonly=True, index=True)
            {aggregate_field} = fields.Many2one(
                {spec.model_name!r},
                string="Bridge Record",
                required=True,
                check_company=True,
                ondelete="restrict",
                index=True,
            )
            aggregate_record_id = fields.Integer(required=True, readonly=True, index=True)
            aggregate_canonical_id = fields.Char(required=True, readonly=True, index=True)
            expected_canonical_version = fields.Integer(required=True, readonly=True)
            aggregate_fingerprint = fields.Char(required=True, readonly=True)
            action = fields.Selection({action_selection!r}, required=True, index=True)
            action_contract_id = fields.Char(required=True, readonly=True, default={spec.action_contract_id!r})
            command_type = fields.Char(required=True, readonly=True, index=True)
            idempotency_key = fields.Char(required=True, readonly=True, index=True)
            payload = fields.Json(required=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            correlation_id = fields.Char(required=True, readonly=True, index=True)
            causation_id = fields.Char(readonly=True, index=True)
            accepted_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
            state = fields.Selection(
                (("accepted", "Accepted"), ("dispatched", "Dispatched")),
                required=True,
                default="accepted",
                readonly=True,
                index=True,
            )

            _idempotency_identity = models.UniqueIndex("(company_id, idempotency_key)")
            _valid_semantic_fingerprint = models.Constraint(
                "CHECK(char_length(semantic_fingerprint) = 64)",
                "Semantic fingerprint must be a SHA-256 hexadecimal digest.",
            )

            @api.model
            def _idempotency_lock_keys(self, company_id, idempotency_key):
                material = f"{{company_id}}:{{idempotency_key}}".encode()
                digest = hashlib.sha256(material).digest()
                return (
                    int.from_bytes(digest[:4], byteorder="big", signed=True),
                    int.from_bytes(digest[4:8], byteorder="big", signed=True),
                )

            @api.model
            def _acquire_idempotency_lock(self, company_id, idempotency_key):
                lock_keys = self._idempotency_lock_keys(company_id, idempotency_key)
                self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", lock_keys)

            @api.model
            def _semantic_fingerprint(
                self,
                aggregate,
                action,
                command_type,
                expected_canonical_version,
                payload,
            ):
                authority_scope = {{
                    field_name: getattr(aggregate, field_name)
                    for field_name in {_py_tuple(tuple(field for field in _AUTHORITY_FIELDS if field != "company_id"))}
                }}
                authority_scope["company_id"] = aggregate.company_id.id
                try:
                    encoded = json.dumps(
                        {{
                            "company_id": aggregate.company_id.id,
                            "aggregate_record_id": aggregate.id,
                            "aggregate_canonical_id": aggregate.canonical_id,
                            "expected_canonical_version": expected_canonical_version,
                            "aggregate_fingerprint": aggregate.fingerprint,
                            "authority_scope": authority_scope,
                            "action": action,
                            "action_contract_id": {spec.action_contract_id!r},
                            "command_type": command_type,
                            "payload": payload,
                        }},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode()
                except (TypeError, ValueError) as exc:
                    raise ValidationError(self.env._("Action payload must be JSON serializable.")) from exc
                return hashlib.sha256(encoded).hexdigest()

            @api.model
            def _capture_action(
                self,
                *,
                aggregate,
                action,
                command_type,
                idempotency_key,
                payload,
                correlation_id,
                expected_canonical_version,
            ):
                aggregate.ensure_one()
                aggregate._validate_identifier(idempotency_key, self.env._("Idempotency key"))
                aggregate._validate_identifier(correlation_id, self.env._("Correlation ID"))
                if action not in _ALLOWED_ACTIONS:
                    raise ValidationError(self.env._("The requested bridge action is not allowlisted."))
                if not isinstance(payload, dict):
                    raise ValidationError(self.env._("Action payload must be an object."))
                if expected_canonical_version != aggregate.canonical_version:
                    raise ValidationError(self.env._("Expected canonical version does not match the aggregate."))

                fingerprint = self._semantic_fingerprint(
                    aggregate,
                    action,
                    command_type,
                    expected_canonical_version,
                    payload,
                )
                self._acquire_idempotency_lock(aggregate.company_id.id, idempotency_key)
                existing = self.sudo().search(
                    [
                        ("company_id", "=", aggregate.company_id.id),
                        ("idempotency_key", "=", idempotency_key),
                    ],
                    limit=1,
                )
                if existing:
                    if existing.semantic_fingerprint != fingerprint:
                        raise ValidationError(
                            self.env._(
                                "Idempotency key was already used for a different aggregate, version, or action."
                            )
                        )
                    return existing

                scope_values = {{
                    field_name: getattr(aggregate, field_name)
                    for field_name in {_py_tuple(tuple(field for field in _AUTHORITY_FIELDS if field != "company_id"))}
                }}
                receipt = self.sudo().create(
                    {{
                        "company_id": aggregate.company_id.id,
                        **scope_values,
                        {aggregate_field!r}: aggregate.id,
                        "aggregate_record_id": aggregate.id,
                        "aggregate_canonical_id": aggregate.canonical_id,
                        "expected_canonical_version": expected_canonical_version,
                        "aggregate_fingerprint": aggregate.fingerprint,
                        "action": action,
                        "action_contract_id": {spec.action_contract_id!r},
                        "command_type": command_type,
                        "idempotency_key": idempotency_key,
                        "payload": payload,
                        "semantic_fingerprint": fingerprint,
                        "correlation_id": correlation_id,
                        "causation_id": aggregate.correlation_id,
                    }}
                )
                self.env[{_queue_model(spec)!r}].sudo()._enqueue_receipt(receipt)
                return receipt
        '''
    )


def _render_native_receipt_model(spec: BridgeSpec) -> str:
    """Render immutable Odoo-authoritative event receipts."""
    class_name = f"{_class_name(spec.domain)}NativeEventReceipt"
    action_values = tuple(action.name for action in spec.actions)
    action_selection = tuple((name, name.replace("_", " ").title()) for name in action_values)
    return _clean(
        f'''
        """Immutable receipts for fixed Odoo-to-FMOS native events."""

        import hashlib
        import json
        import re

        from odoo import api, fields, models
        from odoo.exceptions import ValidationError

        _ALLOWED_ACTIONS = frozenset({_py_tuple(action_values)})
        _IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{{7,127}}\\Z")
        _AUTHORITY_KEYS = frozenset({_py_tuple(_AUTHORITY_FIELDS)})


        class {class_name}(models.Model):
            _name = {_receipt_model(spec)!r}
            _description = {f"{spec.module_title} Native Event Receipt"!r}
            _order = "create_date desc, id desc"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True)
            network_id = fields.Char(required=True, readonly=True, index=True)
            business_id = fields.Char(required=True, readonly=True, index=True)
            app_id = fields.Char(required=True, readonly=True, index=True)
            channel_id = fields.Char(required=True, readonly=True, index=True)
            identity_id = fields.Char(required=True, readonly=True, index=True)
            actor_id = fields.Char(required=True, readonly=True, index=True)
            install_id = fields.Char(required=True, readonly=True, index=True)
            source_id = fields.Many2one(
                {spec.model_name!r},
                string="Odoo Source Record",
                required=True,
                check_company=True,
                ondelete="restrict",
                index=True,
            )
            source_model = fields.Char(required=True, readonly=True, index=True)
            source_record_id = fields.Integer(required=True, readonly=True, index=True)
            source_version = fields.Char(required=True, readonly=True)
            source_fingerprint = fields.Char(required=True, readonly=True)
            source_snapshot = fields.Json(required=True, readonly=True)
            action = fields.Selection({action_selection!r}, required=True, index=True)
            action_contract_id = fields.Char(required=True, readonly=True, default={spec.action_contract_id!r})
            command_type = fields.Char(required=True, readonly=True, index=True)
            idempotency_key = fields.Char(required=True, readonly=True, index=True)
            payload = fields.Json(required=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            correlation_id = fields.Char(required=True, readonly=True, index=True)
            accepted_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
            state = fields.Selection(
                (("accepted", "Accepted"), ("dispatched", "Dispatched")),
                required=True,
                default="accepted",
                readonly=True,
                index=True,
            )

            _idempotency_identity = models.UniqueIndex("(company_id, idempotency_key)")
            _valid_source_fingerprint = models.Constraint(
                "CHECK(char_length(source_fingerprint) = 64)",
                "Source fingerprint must be a SHA-256 hexadecimal digest.",
            )
            _valid_semantic_fingerprint = models.Constraint(
                "CHECK(char_length(semantic_fingerprint) = 64)",
                "Semantic fingerprint must be a SHA-256 hexadecimal digest.",
            )

            @api.model
            def _validate_identifier(self, value, field_label):
                if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                    raise ValidationError(
                        self.env._("%(field)s is not a valid bridge identifier.", field=field_label)
                    )
                return value

            @api.model
            def _idempotency_lock_keys(self, company_id, idempotency_key):
                material = f"{{company_id}}:{{idempotency_key}}".encode()
                digest = hashlib.sha256(material).digest()
                return (
                    int.from_bytes(digest[:4], byteorder="big", signed=True),
                    int.from_bytes(digest[4:8], byteorder="big", signed=True),
                )

            @api.model
            def _acquire_idempotency_lock(self, company_id, idempotency_key):
                lock_keys = self._idempotency_lock_keys(company_id, idempotency_key)
                self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", lock_keys)

            @api.model
            def _validate_authority_scope(self, authority_scope, company):
                if not isinstance(authority_scope, dict) or set(authority_scope) != _AUTHORITY_KEYS:
                    raise ValidationError(
                        self.env._("Authority scope must contain exactly all eight authority fields.")
                    )
                if authority_scope["company_id"] != company.id:
                    raise ValidationError(
                        self.env._("Authority company does not match the native source company scope.")
                    )
                for field_name in _AUTHORITY_KEYS - {{"company_id"}}:
                    self._validate_identifier(
                        authority_scope[field_name],
                        self.env._("Authority field %(field)s", field=field_name),
                    )
                return dict(authority_scope)

            @api.model
            def _source_snapshot(self, source):
                source.ensure_one()
                if source._name != {spec.model_name!r}:
                    raise ValidationError(self.env._("Native event source model does not match the fixed contract."))
                source_company = source.company_id
                company = source_company or self.env.company
                if not company:
                    raise ValidationError(self.env._("Native event source must have an authoritative company."))
                source_version = fields.Datetime.to_string(source.write_date or source.create_date)
                snapshot = {{
                    "company_id": company.id,
                    "company_scope": "source" if source_company else "execution_context",
                    "model": {spec.model_name!r},
                    "record_id": source.id,
                    "version": source_version,
                }}
                encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
                return company, source_version, snapshot, hashlib.sha256(encoded).hexdigest()

            @api.model
            def _semantic_fingerprint(self, snapshot, authority_scope, action, command_type, payload):
                try:
                    encoded = json.dumps(
                        {{
                            "source_snapshot": snapshot,
                            "authority_scope": authority_scope,
                            "action": action,
                            "action_contract_id": {spec.action_contract_id!r},
                            "command_type": command_type,
                            "payload": payload,
                        }},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode()
                except (TypeError, ValueError) as exc:
                    raise ValidationError(self.env._("Action payload must be JSON serializable.")) from exc
                return hashlib.sha256(encoded).hexdigest()

            @api.model
            def _capture_native_action(
                self,
                *,
                source,
                action,
                command_type,
                authority_scope,
                idempotency_key,
                correlation_id,
                payload,
            ):
                source.ensure_one()
                self._validate_identifier(idempotency_key, self.env._("Idempotency key"))
                self._validate_identifier(correlation_id, self.env._("Correlation ID"))
                if action not in _ALLOWED_ACTIONS:
                    raise ValidationError(self.env._("The requested bridge action is not allowlisted."))
                if not isinstance(payload, dict):
                    raise ValidationError(self.env._("Action payload must be an object."))
                company, source_version, snapshot, source_fingerprint = self._source_snapshot(source)
                authority = self._validate_authority_scope(authority_scope, company)
                fingerprint = self._semantic_fingerprint(
                    snapshot,
                    authority,
                    action,
                    command_type,
                    payload,
                )
                self._acquire_idempotency_lock(company.id, idempotency_key)
                existing = self.sudo().search(
                    [
                        ("company_id", "=", company.id),
                        ("idempotency_key", "=", idempotency_key),
                    ],
                    limit=1,
                )
                if existing:
                    if existing.semantic_fingerprint != fingerprint:
                        raise ValidationError(
                            self.env._("Idempotency key was already used for a different native event.")
                        )
                    return existing
                receipt = self.sudo().create(
                    {{
                        "company_id": company.id,
                        **{{
                            field_name: authority[field_name]
                            for field_name in _AUTHORITY_KEYS - {{"company_id"}}
                        }},
                        "source_id": source.id,
                        "source_model": {spec.model_name!r},
                        "source_record_id": source.id,
                        "source_version": source_version,
                        "source_fingerprint": source_fingerprint,
                        "source_snapshot": snapshot,
                        "action": action,
                        "action_contract_id": {spec.action_contract_id!r},
                        "command_type": command_type,
                        "idempotency_key": idempotency_key,
                        "payload": payload,
                        "semantic_fingerprint": fingerprint,
                        "correlation_id": correlation_id,
                    }}
                )
                self.env[{_queue_model(spec)!r}].sudo()._enqueue_receipt(receipt)
                return receipt
        '''
    )


def _render_outbox_model(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_outbox_model(spec)

    class_name = f"{_class_name(spec.domain)}CommandOutbox"
    aggregate_field = f"{spec.domain}_id"
    command_types = tuple(action.command_type for action in spec.actions) + (f"{spec.domain}.reconcile.requested.v1",)
    command_selection = tuple((value, value) for value in command_types)
    return _clean(
        f'''
        """Retryable transactional command outbox with dead-letter recovery."""

        import logging
        import uuid
        from datetime import timedelta

        from odoo import api, fields, models
        from odoo.exceptions import UserError, ValidationError

        _logger = logging.getLogger(__name__)
        _ALLOWED_COMMAND_TYPES = frozenset({_py_tuple(command_types)})
        _MAX_ATTEMPTS = {spec.max_attempts}
        _RETRY_BASE_SECONDS = {spec.retry_base_seconds}
        _RETRY_CAP_SECONDS = {spec.retry_cap_seconds}
        _CRON_BATCH_SIZE = {spec.cron_batch_size}
        _PROCESSING_LEASE_SECONDS = 900


        class {class_name}(models.Model):
            _name = {_queue_model(spec)!r}
            _description = {f"{spec.module_title} Command Outbox"!r}
            _order = "next_attempt_at, id"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True)
            network_id = fields.Char(required=True, readonly=True, index=True)
            business_id = fields.Char(required=True, readonly=True, index=True)
            app_id = fields.Char(required=True, readonly=True, index=True)
            channel_id = fields.Char(required=True, readonly=True, index=True)
            identity_id = fields.Char(required=True, readonly=True, index=True)
            actor_id = fields.Char(required=True, readonly=True, index=True)
            install_id = fields.Char(required=True, readonly=True, index=True)
            receipt_id = fields.Many2one(
                {_receipt_model(spec)!r},
                required=True,
                check_company=True,
                ondelete="restrict",
                index=True,
            )
            {aggregate_field} = fields.Many2one(
                {spec.model_name!r},
                related={f"receipt_id.{aggregate_field}"!r},
                store=True,
                check_company=True,
                index=True,
            )
            aggregate_record_id = fields.Integer(required=True, readonly=True, index=True)
            aggregate_canonical_id = fields.Char(required=True, readonly=True, index=True)
            expected_canonical_version = fields.Integer(required=True, readonly=True)
            aggregate_fingerprint = fields.Char(required=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            action_contract_id = fields.Char(required=True, readonly=True, default={spec.action_contract_id!r})
            command_type = fields.Selection({command_selection!r}, required=True, readonly=True, index=True)
            payload = fields.Json(required=True, readonly=True)
            idempotency_key = fields.Char(required=True, readonly=True, index=True)
            correlation_id = fields.Char(required=True, readonly=True, index=True)
            causation_id = fields.Char(readonly=True, index=True)
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
            next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)
            queued_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
            last_attempt_at = fields.Datetime(readonly=True)
            processing_started_at = fields.Datetime(readonly=True)
            processing_lease_expires_at = fields.Datetime(readonly=True, index=True)
            processing_token = fields.Char(readonly=True, index=True)
            delivered_at = fields.Datetime(readonly=True)
            dead_lettered_at = fields.Datetime(readonly=True)
            dispatch_lag_seconds = fields.Integer(required=True, default=0, readonly=True)
            last_error_code = fields.Char(readonly=True)

            _one_command_per_receipt = models.UniqueIndex("(receipt_id)")
            _nonnegative_attempts = models.Constraint(
                "CHECK(attempts >= 0 AND max_attempts > 0)",
                "Retry counters must be non-negative and bounded.",
            )
            _valid_schedule_state = models.Constraint(
                "CHECK("
                "(state IN ('pending', 'retry') AND next_attempt_at IS NOT NULL "
                "AND processing_lease_expires_at IS NULL AND processing_token IS NULL) OR "
                "(state = 'processing' AND next_attempt_at IS NULL "
                "AND processing_lease_expires_at IS NOT NULL AND processing_token IS NOT NULL) OR "
                "(state IN ('done', 'dead_letter') AND next_attempt_at IS NULL "
                "AND processing_lease_expires_at IS NULL AND processing_token IS NULL)"
                ")",
                "Outbox scheduling fields must match the command state.",
            )

            @api.model
            def _enqueue_receipt(self, receipt):
                receipt.ensure_one()
                if receipt.command_type not in _ALLOWED_COMMAND_TYPES:
                    raise ValidationError(self.env._("Receipt command type is not allowlisted."))
                scope_values = {{
                    field_name: getattr(receipt, field_name)
                    for field_name in {_py_tuple(tuple(field for field in _AUTHORITY_FIELDS if field != "company_id"))}
                }}
                return self.sudo().create(
                    {{
                        "company_id": receipt.company_id.id,
                        **scope_values,
                        "receipt_id": receipt.id,
                        "aggregate_record_id": receipt.aggregate_record_id,
                        "aggregate_canonical_id": receipt.aggregate_canonical_id,
                        "expected_canonical_version": receipt.expected_canonical_version,
                        "aggregate_fingerprint": receipt.aggregate_fingerprint,
                        "semantic_fingerprint": receipt.semantic_fingerprint,
                        "action_contract_id": receipt.action_contract_id,
                        "command_type": receipt.command_type,
                        "payload": receipt.payload,
                        "idempotency_key": receipt.idempotency_key,
                        "correlation_id": receipt.correlation_id,
                        "causation_id": receipt.causation_id,
                    }}
                )

            def _deliver_command(self):
                """Unbound fixed adapter seam implemented only by deployment infrastructure."""
                self.ensure_one()
                raise UserError(self.env._("No FMOS command delivery adapter is installed."))

            def _retry_delay_seconds(self, attempt):
                return min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0)))

            def _mark_failed(self, error):
                self.ensure_one()
                attempts = self.attempts + 1
                values = {{
                    "attempts": attempts,
                    "last_attempt_at": fields.Datetime.now(),
                    "last_error_code": type(error).__name__[:120],
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
                            + timedelta(seconds=self._retry_delay_seconds(attempts)),
                        }}
                    )
                self.sudo().write(values)
                aggregate = self.{aggregate_field}.sudo()
                aggregate_values = {{
                    "reconciliation_state": "error",
                    "last_reconciliation_error_code": type(error).__name__[:120],
                    "reconciliation_retry_count": aggregate.reconciliation_retry_count + 1,
                }}
                if attempts >= self.max_attempts:
                    aggregate_values["reconciliation_dead_letter_count"] = (
                        aggregate.reconciliation_dead_letter_count + 1
                    )
                aggregate.write(aggregate_values)

            def _lock_current_claim(self, processing_token):
                self.ensure_one()
                if not processing_token:
                    return False
                self.flush_recordset(
                    ["state", "processing_lease_expires_at", "processing_token"]
                )
                self.env.cr.execute(
                    f"""
                    SELECT id
                    FROM "{{self._table}}"
                    WHERE id = %s
                      AND state = 'processing'
                      AND processing_token = %s
                      AND processing_lease_expires_at > %s
                    FOR UPDATE
                    """,
                    (self.id, processing_token, fields.Datetime.now()),
                )
                owned = self.env.cr.fetchone() is not None
                self.invalidate_recordset(
                    ["state", "processing_lease_expires_at", "processing_token"],
                    flush=False,
                )
                return owned

            def _process_one(self, processing_token=None):
                self.ensure_one()
                claim_token = processing_token or self.processing_token
                if not self._lock_current_claim(claim_token):
                    return False
                try:
                    with self.env.cr.savepoint():
                        self._deliver_command()
                except Exception as exc:
                    _logger.exception("FMOS command delivery failed for outbox row %s", self.id)
                    self._mark_failed(exc)
                    return False
                delivered_at = fields.Datetime.now()
                dispatch_lag_seconds = max(0, int((delivered_at - self.queued_at).total_seconds()))
                self.sudo().write(
                    {{
                        "state": "done",
                        "attempts": self.attempts + 1,
                        "delivered_at": delivered_at,
                        "dispatch_lag_seconds": dispatch_lag_seconds,
                        "last_error_code": False,
                        "next_attempt_at": False,
                        "processing_started_at": False,
                        "processing_lease_expires_at": False,
                        "processing_token": False,
                    }}
                )
                self.receipt_id.sudo().write({{"state": "dispatched"}})
                return True

            @api.model
            def _claim_due_commands(self):
                """Atomically claim due or lease-expired commands without worker overlap."""
                now = fields.Datetime.now()
                lease_expires_at = now + timedelta(seconds=_PROCESSING_LEASE_SECONDS)
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
                query = f"""
                    WITH candidates AS (
                        SELECT id
                        FROM "{{self._table}}"
                        WHERE (
                            state IN ('pending', 'retry')
                            AND next_attempt_at <= %s
                        ) OR (
                            state = 'processing'
                            AND processing_lease_expires_at <= %s
                        )
                        ORDER BY COALESCE(next_attempt_at, processing_lease_expires_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE "{{self._table}}" AS command
                    SET state = 'processing',
                        next_attempt_at = NULL,
                        processing_started_at = %s,
                        processing_lease_expires_at = %s,
                        processing_token = %s,
                        last_attempt_at = %s,
                        write_date = %s,
                        write_uid = %s
                    FROM candidates
                    WHERE command.id = candidates.id
                    RETURNING command.id
                """
                self.env.cr.execute(
                    query,
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
                claimed = self.browse([row[0] for row in self.env.cr.fetchall()])
                claimed.invalidate_recordset(
                    [
                        "state",
                        "next_attempt_at",
                        "processing_started_at",
                        "processing_lease_expires_at",
                        "processing_token",
                        "last_attempt_at",
                        "write_date",
                        "write_uid",
                    ],
                    flush=False,
                )
                return claimed

            @api.model
            def _cron_process_due_commands(self):
                now = fields.Datetime.now()
                domain = [
                    "|",
                    "&",
                    ("state", "in", ("pending", "retry")),
                    ("next_attempt_at", "<=", now),
                    "&",
                    ("state", "=", "processing"),
                    ("processing_lease_expires_at", "<=", now),
                ]
                remaining = self.search_count(domain)
                commands = self._claim_due_commands()
                for command in commands:
                    command._process_one(command.processing_token)
                    remaining -= 1
                    if not self.env["ir.cron"]._commit_progress(1, remaining=remaining):
                        break

            def action_recover(self):
                """Return selected dead letters to the retry queue."""
                if not self.env.user.has_group({f"{spec.module_name}.group_bridge_admin"!r}):
                    raise UserError(self.env._("Bridge administrator access is required."))
                recoverable = self.filtered(lambda command: command.state == "dead_letter")
                recoverable.sudo().write(
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
                recoverable.mapped({aggregate_field!r}).sudo().write(
                    {{
                        "reconciliation_state": "pending",
                        "last_reconciliation_error_code": False,
                    }}
                )
                return True
        '''
    )


def _render_native_outbox_model(spec: BridgeSpec) -> str:
    """Render a leased native-event outbox with a fixed unbound delivery seam."""
    class_name = f"{_class_name(spec.domain)}NativeCommandOutbox"
    command_types = tuple(action.command_type for action in spec.actions)
    command_selection = tuple((value, value) for value in command_types)
    return _clean(
        f'''
        """Retryable native-event outbox with leased atomic claims and recovery."""

        import logging
        import uuid
        from datetime import timedelta

        from odoo import api, fields, models
        from odoo.exceptions import UserError, ValidationError

        _logger = logging.getLogger(__name__)
        _ALLOWED_COMMAND_TYPES = frozenset({_py_tuple(command_types)})
        _MAX_ATTEMPTS = {spec.max_attempts}
        _RETRY_BASE_SECONDS = {spec.retry_base_seconds}
        _RETRY_CAP_SECONDS = {spec.retry_cap_seconds}
        _CRON_BATCH_SIZE = {spec.cron_batch_size}
        _PROCESSING_LEASE_SECONDS = 900


        class {class_name}(models.Model):
            _name = {_queue_model(spec)!r}
            _description = {f"{spec.module_title} Native Command Outbox"!r}
            _order = "next_attempt_at, id"
            _check_company_auto = True

            company_id = fields.Many2one("res.company", required=True, index=True)
            network_id = fields.Char(required=True, readonly=True, index=True)
            business_id = fields.Char(required=True, readonly=True, index=True)
            app_id = fields.Char(required=True, readonly=True, index=True)
            channel_id = fields.Char(required=True, readonly=True, index=True)
            identity_id = fields.Char(required=True, readonly=True, index=True)
            actor_id = fields.Char(required=True, readonly=True, index=True)
            install_id = fields.Char(required=True, readonly=True, index=True)
            receipt_id = fields.Many2one(
                {_receipt_model(spec)!r},
                required=True,
                check_company=True,
                ondelete="restrict",
                index=True,
            )
            source_id = fields.Many2one(
                {spec.model_name!r},
                related="receipt_id.source_id",
                store=True,
                check_company=True,
                index=True,
            )
            source_model = fields.Char(required=True, readonly=True, index=True)
            source_record_id = fields.Integer(required=True, readonly=True, index=True)
            source_version = fields.Char(required=True, readonly=True)
            source_fingerprint = fields.Char(required=True, readonly=True)
            source_snapshot = fields.Json(required=True, readonly=True)
            semantic_fingerprint = fields.Char(required=True, readonly=True)
            action_contract_id = fields.Char(required=True, readonly=True, default={spec.action_contract_id!r})
            command_type = fields.Selection({command_selection!r}, required=True, readonly=True, index=True)
            payload = fields.Json(required=True, readonly=True)
            idempotency_key = fields.Char(required=True, readonly=True, index=True)
            correlation_id = fields.Char(required=True, readonly=True, index=True)
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
            next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)
            queued_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
            last_attempt_at = fields.Datetime(readonly=True)
            processing_started_at = fields.Datetime(readonly=True)
            processing_lease_expires_at = fields.Datetime(readonly=True, index=True)
            processing_token = fields.Char(readonly=True, index=True)
            delivered_at = fields.Datetime(readonly=True)
            dead_lettered_at = fields.Datetime(readonly=True)
            dispatch_lag_seconds = fields.Integer(required=True, default=0, readonly=True)
            last_error_code = fields.Char(readonly=True)

            _one_command_per_receipt = models.UniqueIndex("(receipt_id)")
            _nonnegative_attempts = models.Constraint(
                "CHECK(attempts >= 0 AND max_attempts > 0)",
                "Retry counters must be non-negative and bounded.",
            )
            _valid_schedule_state = models.Constraint(
                "CHECK("
                "(state IN ('pending', 'retry') AND next_attempt_at IS NOT NULL "
                "AND processing_lease_expires_at IS NULL AND processing_token IS NULL) OR "
                "(state = 'processing' AND next_attempt_at IS NULL "
                "AND processing_lease_expires_at IS NOT NULL AND processing_token IS NOT NULL) OR "
                "(state IN ('done', 'dead_letter') AND next_attempt_at IS NULL "
                "AND processing_lease_expires_at IS NULL AND processing_token IS NULL)"
                ")",
                "Outbox scheduling fields must match the command state.",
            )

            @api.model
            def _enqueue_receipt(self, receipt):
                receipt.ensure_one()
                if receipt.command_type not in _ALLOWED_COMMAND_TYPES:
                    raise ValidationError(self.env._("Receipt command type is not allowlisted."))
                scope_values = {{
                    field_name: getattr(receipt, field_name)
                    for field_name in {_py_tuple(tuple(field for field in _AUTHORITY_FIELDS if field != "company_id"))}
                }}
                return self.sudo().create(
                    {{
                        "company_id": receipt.company_id.id,
                        **scope_values,
                        "receipt_id": receipt.id,
                        "source_model": receipt.source_model,
                        "source_record_id": receipt.source_record_id,
                        "source_version": receipt.source_version,
                        "source_fingerprint": receipt.source_fingerprint,
                        "source_snapshot": receipt.source_snapshot,
                        "semantic_fingerprint": receipt.semantic_fingerprint,
                        "action_contract_id": receipt.action_contract_id,
                        "command_type": receipt.command_type,
                        "payload": receipt.payload,
                        "idempotency_key": receipt.idempotency_key,
                        "correlation_id": receipt.correlation_id,
                    }}
                )

            def _deliver_command(self):
                """Unbound fixed adapter seam implemented only by deployment infrastructure."""
                self.ensure_one()
                raise UserError(self.env._("No FMOS command delivery adapter is installed."))

            def _retry_delay_seconds(self, attempt):
                return min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0)))

            def _mark_failed(self, error):
                self.ensure_one()
                attempts = self.attempts + 1
                values = {{
                    "attempts": attempts,
                    "last_attempt_at": fields.Datetime.now(),
                    "last_error_code": type(error).__name__[:120],
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
                            + timedelta(seconds=self._retry_delay_seconds(attempts)),
                        }}
                    )
                self.sudo().write(values)

            def _lock_current_claim(self, processing_token):
                self.ensure_one()
                if not processing_token:
                    return False
                self.flush_recordset(
                    ["state", "processing_lease_expires_at", "processing_token"]
                )
                self.env.cr.execute(
                    f"""
                    SELECT id
                    FROM "{{self._table}}"
                    WHERE id = %s
                      AND state = 'processing'
                      AND processing_token = %s
                      AND processing_lease_expires_at > %s
                    FOR UPDATE
                    """,
                    (self.id, processing_token, fields.Datetime.now()),
                )
                owned = self.env.cr.fetchone() is not None
                self.invalidate_recordset(
                    ["state", "processing_lease_expires_at", "processing_token"],
                    flush=False,
                )
                return owned

            def _process_one(self, processing_token=None):
                self.ensure_one()
                claim_token = processing_token or self.processing_token
                if not self._lock_current_claim(claim_token):
                    return False
                try:
                    with self.env.cr.savepoint():
                        self._deliver_command()
                except Exception as exc:
                    _logger.exception("FMOS native event delivery failed for outbox row %s", self.id)
                    self._mark_failed(exc)
                    return False
                delivered_at = fields.Datetime.now()
                dispatch_lag_seconds = max(0, int((delivered_at - self.queued_at).total_seconds()))
                self.sudo().write(
                    {{
                        "state": "done",
                        "attempts": self.attempts + 1,
                        "delivered_at": delivered_at,
                        "dispatch_lag_seconds": dispatch_lag_seconds,
                        "last_error_code": False,
                        "next_attempt_at": False,
                        "processing_started_at": False,
                        "processing_lease_expires_at": False,
                        "processing_token": False,
                    }}
                )
                self.receipt_id.sudo().write({{"state": "dispatched"}})
                return True

            @api.model
            def _claim_due_commands(self):
                """Atomically claim due or lease-expired commands without worker overlap."""
                now = fields.Datetime.now()
                lease_expires_at = now + timedelta(seconds=_PROCESSING_LEASE_SECONDS)
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
                query = f"""
                    WITH candidates AS (
                        SELECT id
                        FROM "{{self._table}}"
                        WHERE (
                            state IN ('pending', 'retry')
                            AND next_attempt_at <= %s
                        ) OR (
                            state = 'processing'
                            AND processing_lease_expires_at <= %s
                        )
                        ORDER BY COALESCE(next_attempt_at, processing_lease_expires_at), id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE "{{self._table}}" AS command
                    SET state = 'processing',
                        next_attempt_at = NULL,
                        processing_started_at = %s,
                        processing_lease_expires_at = %s,
                        processing_token = %s,
                        last_attempt_at = %s,
                        write_date = %s,
                        write_uid = %s
                    FROM candidates
                    WHERE command.id = candidates.id
                    RETURNING command.id
                """
                self.env.cr.execute(
                    query,
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
                claimed = self.browse([row[0] for row in self.env.cr.fetchall()])
                claimed.invalidate_recordset(
                    [
                        "state",
                        "next_attempt_at",
                        "processing_started_at",
                        "processing_lease_expires_at",
                        "processing_token",
                        "last_attempt_at",
                        "write_date",
                        "write_uid",
                    ],
                    flush=False,
                )
                return claimed

            @api.model
            def _cron_process_due_commands(self):
                now = fields.Datetime.now()
                domain = [
                    "|",
                    "&",
                    ("state", "in", ("pending", "retry")),
                    ("next_attempt_at", "<=", now),
                    "&",
                    ("state", "=", "processing"),
                    ("processing_lease_expires_at", "<=", now),
                ]
                remaining = self.search_count(domain)
                commands = self._claim_due_commands()
                for command in commands:
                    command._process_one(command.processing_token)
                    remaining -= 1
                    if not self.env["ir.cron"]._commit_progress(1, remaining=remaining):
                        break

            def action_recover(self):
                """Return selected dead letters to the retry queue."""
                if not self.env.user.has_group({f"{spec.module_name}.group_bridge_admin"!r}):
                    raise UserError(self.env._("Bridge administrator access is required."))
                recoverable = self.filtered(lambda command: command.state == "dead_letter")
                recoverable.sudo().write(
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


def _render_groups(spec: BridgeSpec) -> str:
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="module_category_fmos_bridge" model="ir.module.category">
                <field name="name">FMOS Bridge Operations</field>
                <field name="sequence">90</field>
            </record>
            <record id="privilege_fmos_bridge" model="res.groups.privilege">
                <field name="name">Bridge Operations</field>
                <field name="category_id" ref="{spec.module_name}.module_category_fmos_bridge"/>
                <field name="sequence">10</field>
            </record>
            <record id="group_bridge_operator" model="res.groups">
                <field name="name">Bridge Operator</field>
                <field name="privilege_id" ref="{spec.module_name}.privilege_fmos_bridge"/>
            </record>
            <record id="group_bridge_admin" model="res.groups">
                <field name="name">Bridge Administrator</field>
                <field name="privilege_id" ref="{spec.module_name}.privilege_fmos_bridge"/>
                <field name="implied_ids" eval="[(4, ref('{spec.module_name}.group_bridge_operator'))]"/>
            </record>
        </odoo>
        """
    )


def _render_access_csv(spec: BridgeSpec) -> str:
    rows = [
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink",
    ]
    models = [
        ("bridge_record", spec.model_name),
        ("action_receipt", _receipt_model(spec)),
        ("command_outbox", _queue_model(spec)),
    ]
    if spec.bridge_mode == "odoo_native_extension":
        models = models[1:]
    for slug, model_name in models:
        model_ref = _xml_id(model_name)
        ledger_permissions = "1,0,0,0"
        admin_permissions = "1,0,0,0"
        rows.append(
            f"access_{slug}_operator,{slug} operator,{model_ref},"
            f"{spec.module_name}.group_bridge_operator,{ledger_permissions}"
        )
        rows.append(
            f"access_{slug}_admin,{slug} admin,{model_ref},{spec.module_name}.group_bridge_admin,{admin_permissions}"
        )
    return "\n".join(rows) + "\n"


def _render_record_rules(spec: BridgeSpec) -> str:
    models = [
        ("bridge_record", spec.model_name),
        ("action_receipt", _receipt_model(spec)),
        ("command_outbox", _queue_model(spec)),
    ]
    if spec.bridge_mode == "odoo_native_extension":
        models = models[1:]
    records = "\n".join(
        textwrap.dedent(
            f"""
                <record id="rule_{slug}_company" model="ir.rule">
                    <field name="name">{spec.module_title}: {slug.replace("_", " ").title()} Company</field>
                    <field name="model_id" ref="{_xml_id(model_name)}"/>
                    <field name="global" eval="True"/>
                    <field name="domain_force">[('company_id', 'in', company_ids)]</field>
                </record>
            """
        ).rstrip()
        for slug, model_name in models
    )
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
        {records}
        </odoo>
        """
    )


def _render_cron(spec: BridgeSpec) -> str:
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo noupdate="1">
            <record id="ir_cron_process_bridge_commands" model="ir.cron">
                <field name="name">{spec.module_title}: Process Commands</field>
                <field name="model_id" ref="{_xml_id(_queue_model(spec))}"/>
                <field name="state">code</field>
                <field name="code">model._cron_process_due_commands()</field>
                <field name="interval_number">1</field>
                <field name="interval_type">minutes</field>
                <field name="priority">5</field>
                <field name="active" eval="True"/>
            </record>
        </odoo>
        """
    )


def _render_bridge_views(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _clean(
            """
            <?xml version="1.0" encoding="utf-8"?>
            <odoo/>
            """
        )

    field_names = [field.name for field in spec.fields]
    list_fields = "\n".join(f'                    <field name="{name}"/>' for name in field_names[:4])
    form_fields = "\n".join(f'                        <field name="{name}"/>' for name in field_names)
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_bridge_record_list" model="ir.ui.view">
                <field name="name">{spec.model_name}.bridge.list</field>
                <field name="model">{spec.model_name}</field>
                <field name="arch" type="xml">
                    <list create="0" delete="0">
                        <field name="company_id" groups="base.group_multi_company"/>
                        <field name="install_id"/>
                        <field name="canonical_id"/>
                        <field name="canonical_version"/>
        {list_fields}
                        <field name="origin"/>
                        <field name="reconciliation_state"/>
                        <field name="reconciliation_lag"/>
                        <field name="last_projection_at"/>
                    </list>
                </field>
            </record>
            <record id="view_bridge_record_form" model="ir.ui.view">
                <field name="name">{spec.model_name}.bridge.form</field>
                <field name="model">{spec.model_name}</field>
                <field name="arch" type="xml">
                    <form create="0" delete="0">
                        <header>
                            <button name="action_request_reconciliation" type="object"
                                    string="Request Reconciliation"
                                    groups="{spec.module_name}.group_bridge_operator"/>
                        </header>
                        <sheet>
                            <group>
                                <group>
                                    <field name="company_id" groups="base.group_multi_company"/>
                                    <field name="network_id" readonly="1"/>
                                    <field name="business_id" readonly="1"/>
                                    <field name="app_id" readonly="1"/>
                                    <field name="channel_id" readonly="1"/>
                                    <field name="identity_id" readonly="1"/>
                                    <field name="actor_id" readonly="1"/>
                                    <field name="install_id" readonly="1"/>
                                    <field name="canonical_id" readonly="1"/>
                                    <field name="canonical_version" readonly="1"/>
                                    <field name="origin" readonly="1"/>
                                </group>
                                <group>
                                    <field name="fingerprint" readonly="1"/>
                                    <field name="correlation_id" readonly="1"/>
                                    <field name="causation_id" readonly="1"/>
                                    <field name="last_projection_at" readonly="1"/>
                                </group>
                            </group>
                            <group string="Reconciliation">
                                <field name="reconciliation_state" readonly="1"/>
                                <field name="source_version" readonly="1"/>
                                <field name="destination_version" readonly="1"/>
                                <field name="source_fingerprint" readonly="1"/>
                                <field name="destination_fingerprint" readonly="1"/>
                                <field name="last_reconciled_at" readonly="1"/>
                                <field name="last_reconciliation_error_code" readonly="1"/>
                                <field name="reconciliation_lag" readonly="1"/>
                                <field name="reconciliation_retry_count" readonly="1"/>
                                <field name="reconciliation_dead_letter_count" readonly="1"/>
                            </group>
                            <group string="Canonical Data">
        {form_fields}
                            </group>
                        </sheet>
                    </form>
                </field>
            </record>
            <record id="action_bridge_records" model="ir.actions.act_window">
                <field name="name">{spec.module_title}</field>
                <field name="res_model">{spec.model_name}</field>
                <field name="view_mode">list,form</field>
            </record>
        </odoo>
        """
    )


def _render_receipt_views(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_receipt_views(spec)

    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_action_receipt_list" model="ir.ui.view">
                <field name="name">{_receipt_model(spec)}.list</field>
                <field name="model">{_receipt_model(spec)}</field>
                <field name="arch" type="xml">
                    <list create="0" edit="0" delete="0">
                        <field name="company_id" groups="base.group_multi_company"/>
                        <field name="install_id"/>
                        <field name="aggregate_canonical_id"/>
                        <field name="expected_canonical_version"/>
                        <field name="action"/>
                        <field name="command_type"/>
                        <field name="idempotency_key"/>
                        <field name="correlation_id"/>
                        <field name="state"/>
                        <field name="create_date"/>
                    </list>
                </field>
            </record>
            <record id="view_action_receipt_form" model="ir.ui.view">
                <field name="name">{_receipt_model(spec)}.form</field>
                <field name="model">{_receipt_model(spec)}</field>
                <field name="arch" type="xml">
                    <form create="0" edit="0" delete="0">
                        <sheet>
                            <group>
                                <field name="company_id"/>
                                <field name="network_id"/>
                                <field name="business_id"/>
                                <field name="app_id"/>
                                <field name="channel_id"/>
                                <field name="identity_id"/>
                                <field name="actor_id"/>
                                <field name="install_id"/>
                                <field name="aggregate_record_id"/>
                                <field name="aggregate_canonical_id"/>
                                <field name="expected_canonical_version"/>
                                <field name="aggregate_fingerprint"/>
                                <field name="action"/>
                                <field name="action_contract_id"/>
                                <field name="command_type"/>
                                <field name="idempotency_key"/>
                                <field name="semantic_fingerprint"/>
                                <field name="correlation_id"/>
                                <field name="causation_id"/>
                                <field name="state"/>
                                <field name="payload" widget="json"/>
                            </group>
                        </sheet>
                    </form>
                </field>
            </record>
            <record id="action_bridge_receipts" model="ir.actions.act_window">
                <field name="name">Action Receipts</field>
                <field name="res_model">{_receipt_model(spec)}</field>
                <field name="view_mode">list,form</field>
            </record>
        </odoo>
        """
    )


def _render_outbox_views(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_outbox_views(spec)

    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_command_outbox_list" model="ir.ui.view">
                <field name="name">{_queue_model(spec)}.list</field>
                <field name="model">{_queue_model(spec)}</field>
                <field name="arch" type="xml">
                    <list create="0" edit="0" delete="0"
                          decoration-danger="state == 'dead_letter'"
                          decoration-success="state == 'done'">
                        <field name="company_id" groups="base.group_multi_company"/>
                        <field name="install_id"/>
                        <field name="aggregate_canonical_id"/>
                        <field name="expected_canonical_version"/>
                        <field name="command_type"/>
                        <field name="idempotency_key"/>
                        <field name="state"/>
                        <field name="attempts"/>
                        <field name="max_attempts"/>
                        <field name="next_attempt_at"/>
                        <field name="dispatch_lag_seconds"/>
                        <field name="last_error_code"/>
                    </list>
                </field>
            </record>
            <record id="view_command_outbox_form" model="ir.ui.view">
                <field name="name">{_queue_model(spec)}.form</field>
                <field name="model">{_queue_model(spec)}</field>
                <field name="arch" type="xml">
                    <form create="0" edit="0" delete="0">
                        <header>
                            <button name="action_recover" type="object" string="Recover"
                                    invisible="state != 'dead_letter'"
                                    groups="{spec.module_name}.group_bridge_admin"/>
                            <field name="state" widget="statusbar"/>
                        </header>
                        <sheet>
                            <group>
                                <group>
                                    <field name="company_id"/>
                                    <field name="network_id"/>
                                    <field name="business_id"/>
                                    <field name="app_id"/>
                                    <field name="channel_id"/>
                                    <field name="identity_id"/>
                                    <field name="actor_id"/>
                                    <field name="install_id"/>
                                    <field name="receipt_id"/>
                                    <field name="aggregate_record_id"/>
                                    <field name="aggregate_canonical_id"/>
                                    <field name="expected_canonical_version"/>
                                    <field name="aggregate_fingerprint"/>
                                    <field name="semantic_fingerprint"/>
                                    <field name="action_contract_id"/>
                                    <field name="command_type"/>
                                    <field name="idempotency_key"/>
                                </group>
                                <group>
                                    <field name="attempts"/>
                                    <field name="max_attempts"/>
                                    <field name="next_attempt_at"/>
                                    <field name="queued_at"/>
                                    <field name="last_attempt_at"/>
                                    <field name="processing_started_at"/>
                                    <field name="processing_lease_expires_at"/>
                                    <field name="delivered_at"/>
                                    <field name="dead_lettered_at"/>
                                    <field name="dispatch_lag_seconds"/>
                                </group>
                            </group>
                            <group>
                                <field name="correlation_id"/>
                                <field name="causation_id"/>
                                <field name="last_error_code"/>
                                <field name="payload" widget="json"/>
                            </group>
                        </sheet>
                    </form>
                </field>
            </record>
            <record id="action_bridge_outbox" model="ir.actions.act_window">
                <field name="name">Command Outbox</field>
                <field name="res_model">{_queue_model(spec)}</field>
                <field name="view_mode">list,form</field>
            </record>
        </odoo>
        """
    )


def _render_native_receipt_views(spec: BridgeSpec) -> str:
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_action_receipt_list" model="ir.ui.view">
                <field name="name">{_receipt_model(spec)}.list</field>
                <field name="model">{_receipt_model(spec)}</field>
                <field name="arch" type="xml">
                    <list create="0" edit="0" delete="0">
                        <field name="company_id" groups="base.group_multi_company"/>
                        <field name="source_model"/>
                        <field name="source_record_id"/>
                        <field name="source_version"/>
                        <field name="action"/>
                        <field name="command_type"/>
                        <field name="idempotency_key"/>
                        <field name="correlation_id"/>
                        <field name="state"/>
                        <field name="create_date"/>
                    </list>
                </field>
            </record>
            <record id="view_action_receipt_form" model="ir.ui.view">
                <field name="name">{_receipt_model(spec)}.form</field>
                <field name="model">{_receipt_model(spec)}</field>
                <field name="arch" type="xml">
                    <form create="0" edit="0" delete="0">
                        <sheet>
                            <group>
                                <field name="company_id"/>
                                <field name="source_id"/>
                                <field name="source_model"/>
                                <field name="source_record_id"/>
                                <field name="source_version"/>
                                <field name="source_fingerprint"/>
                                <field name="source_snapshot" widget="json"/>
                                <field name="action"/>
                                <field name="action_contract_id"/>
                                <field name="command_type"/>
                                <field name="idempotency_key"/>
                                <field name="semantic_fingerprint"/>
                                <field name="correlation_id"/>
                                <field name="state"/>
                                <field name="payload" widget="json"/>
                            </group>
                        </sheet>
                    </form>
                </field>
            </record>
            <record id="action_bridge_receipts" model="ir.actions.act_window">
                <field name="name">Native Event Receipts</field>
                <field name="res_model">{_receipt_model(spec)}</field>
                <field name="view_mode">list,form</field>
            </record>
        </odoo>
        """
    )


def _render_native_outbox_views(spec: BridgeSpec) -> str:
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <record id="view_command_outbox_list" model="ir.ui.view">
                <field name="name">{_queue_model(spec)}.list</field>
                <field name="model">{_queue_model(spec)}</field>
                <field name="arch" type="xml">
                    <list create="0" edit="0" delete="0"
                          decoration-danger="state == 'dead_letter'"
                          decoration-success="state == 'done'">
                        <field name="company_id" groups="base.group_multi_company"/>
                        <field name="source_model"/>
                        <field name="source_record_id"/>
                        <field name="source_version"/>
                        <field name="command_type"/>
                        <field name="idempotency_key"/>
                        <field name="state"/>
                        <field name="attempts"/>
                        <field name="max_attempts"/>
                        <field name="next_attempt_at"/>
                        <field name="processing_lease_expires_at"/>
                        <field name="dispatch_lag_seconds"/>
                        <field name="last_error_code"/>
                    </list>
                </field>
            </record>
            <record id="view_command_outbox_form" model="ir.ui.view">
                <field name="name">{_queue_model(spec)}.form</field>
                <field name="model">{_queue_model(spec)}</field>
                <field name="arch" type="xml">
                    <form create="0" edit="0" delete="0">
                        <header>
                            <button name="action_recover" type="object" string="Recover"
                                    invisible="state != 'dead_letter'"
                                    groups="{spec.module_name}.group_bridge_admin"/>
                            <field name="state" widget="statusbar"/>
                        </header>
                        <sheet>
                            <group>
                                <field name="company_id"/>
                                <field name="receipt_id"/>
                                <field name="source_id"/>
                                <field name="source_model"/>
                                <field name="source_record_id"/>
                                <field name="source_version"/>
                                <field name="source_fingerprint"/>
                                <field name="source_snapshot" widget="json"/>
                                <field name="semantic_fingerprint"/>
                                <field name="action_contract_id"/>
                                <field name="command_type"/>
                                <field name="idempotency_key"/>
                                <field name="attempts"/>
                                <field name="max_attempts"/>
                                <field name="next_attempt_at"/>
                                <field name="queued_at"/>
                                <field name="last_attempt_at"/>
                                <field name="processing_started_at"/>
                                <field name="processing_lease_expires_at"/>
                                <field name="delivered_at"/>
                                <field name="dead_lettered_at"/>
                                <field name="dispatch_lag_seconds"/>
                                <field name="correlation_id"/>
                                <field name="last_error_code"/>
                                <field name="payload" widget="json"/>
                            </group>
                        </sheet>
                    </form>
                </field>
            </record>
            <record id="action_bridge_outbox" model="ir.actions.act_window">
                <field name="name">Native Command Outbox</field>
                <field name="res_model">{_queue_model(spec)}</field>
                <field name="view_mode">list,form</field>
            </record>
        </odoo>
        """
    )


def _render_menu(spec: BridgeSpec) -> str:
    record_menu = (
        """
        <menuitem id="menu_bridge_records" name="Canonical Records"
                  parent="menu_bridge_root" action="action_bridge_records" sequence="10"/>
        """
        if spec.bridge_mode == "fmos_authoritative"
        else ""
    )
    return _clean(
        f"""
        <?xml version="1.0" encoding="utf-8"?>
        <odoo>
            <menuitem id="menu_bridge_root" name="{spec.module_title}"
                      sequence="90" groups="{spec.module_name}.group_bridge_operator"/>
        {textwrap.dedent(record_menu).strip()}
            <menuitem id="menu_bridge_receipts" name="Action Receipts"
                      parent="menu_bridge_root" action="action_bridge_receipts" sequence="20"/>
            <menuitem id="menu_bridge_outbox" name="Command Outbox"
                      parent="menu_bridge_root" action="action_bridge_outbox" sequence="30"/>
        </odoo>
        """
    )


def _render_install_contract(spec: BridgeSpec) -> str:
    fmos_authoritative = spec.bridge_mode == "fmos_authoritative"
    payload = {
        "schema_version": "1.0",
        "odoo_version": "19.0",
        "module": spec.module_name,
        "bridge_mode": spec.bridge_mode,
        "authoritative_writer": "fmos" if fmos_authoritative else "odoo",
        "aggregate_model": spec.model_name,
        "receipt_model": _receipt_model(spec),
        "outbox_model": _queue_model(spec),
        "contracts": {
            "provider": spec.provider_contract_id,
            "wrapper": spec.wrapper_contract_id,
            "projection": spec.projection_contract_id,
            "action": spec.action_contract_id,
        },
        "dependencies": list(_dependencies(spec)),
        "directions": {
            "fmos_to_odoo": "versioned projection into generated aggregate" if fmos_authoritative else "prohibited",
            "odoo_to_fmos": "fixed action receipts and command outbox"
            if fmos_authoritative
            else "fixed native-record snapshots, event receipts, and command outbox",
        },
        "authority_fields": list(_AUTHORITY_FIELDS),
        "authority_semantics": {
            "required": True,
            "fallbacks": False,
            "company_isolation": "active Odoo company scope plus global company record rules",
            "aggregate_identity": "all eight authority fields plus canonical_id"
            if fmos_authoritative
            else "all eight authority fields plus native source model, source record ID, and source version",
            "command_snapshot": "receipt and outbox preserve every authority field"
            if fmos_authoritative
            else "receipt and outbox preserve every authority field plus a non-PII Odoo source snapshot",
            "cross_scope_reassignment": "prohibited",
        },
        "projection_fields": [field.name for field in spec.fields] if fmos_authoritative else [],
        "projection_semantics": (
            {
                "concurrency": "transaction-scoped PostgreSQL advisory lock over all authority fields and canonical_id",
                "version": "monotonic; same version requires identical canonical fingerprint",
                "fingerprint_material": [
                    "authority_scope",
                    "canonical_id",
                    "canonical_version",
                    "values",
                ],
            }
            if fmos_authoritative
            else {
                "supported": False,
                "reason": "Odoo is the sole native aggregate writer; no apply_fmos_projection or create/update path exists",
            }
        ),
        "allowlisted_actions": {action.name: action.command_type for action in spec.actions}
        | ({"reconcile": f"{spec.domain}.reconcile.requested.v1"} if fmos_authoritative else {}),
        "action_semantics": {
            "expected_version": "required and equal to aggregate canonical_version"
            if fmos_authoritative
            else "captured from the immutable Odoo source snapshot",
            "idempotency_identity": ["company_id", "idempotency_key"],
            "concurrency": "transaction-scoped PostgreSQL advisory lock before idempotency search/create",
            "semantic_fingerprint_material": [
                *(
                    [
                        "company_id",
                        "aggregate_record_id",
                        "aggregate_canonical_id",
                        "expected_canonical_version",
                        "aggregate_fingerprint",
                        "authority_scope",
                    ]
                    if fmos_authoritative
                    else ["authority_scope", "source_snapshot"]
                ),
                "action",
                "action_contract_id",
                "command_type",
                "payload",
            ],
            "ledger_rpc_crud": "read-only; fixed guarded methods own state transitions",
        },
        "retry_dead_letter_recovery": {
            "max_attempts": spec.max_attempts,
            "retry_base_seconds": spec.retry_base_seconds,
            "retry_cap_seconds": spec.retry_cap_seconds,
            "recovery": "bridge administrator fixed action_recover method",
            "claim": "atomic FOR UPDATE SKIP LOCKED claim",
            "processing_lease_seconds": 900,
            "lease_recovery": "expired processing rows are reclaimed atomically",
            "lease_fencing": "unguessable processing token plus row lock prevents stale worker transitions",
            "terminal_next_attempt_at": None,
        },
        "reconciliation": (
            {
                "persisted_fields": [
                    "reconciliation_state",
                    "source_version",
                    "destination_version",
                    "source_fingerprint",
                    "destination_fingerprint",
                    "last_reconciled_at",
                    "last_reconciliation_error_code",
                    "reconciliation_lag",
                    "reconciliation_retry_count",
                    "reconciliation_dead_letter_count",
                ],
                "observation_api": "apply_reconciliation_observation",
            }
            if fmos_authoritative
            else {"supported": False, "reason": "native aggregate ownership remains exclusively in Odoo"}
        ),
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
            "status": "unbound",
            "seam": "deployment addon overrides the fixed _deliver_command method",
            "runtime_dispatch": False,
            "guarantee": "the generated addon cannot deliver until an explicit adapter is installed",
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _render_migration(stage: str, spec: BridgeSpec) -> str:
    return _clean(
        f'''
        """{stage.title()}-migration skeleton for {spec.module_name} {spec.version}."""

        import logging

        _logger = logging.getLogger(__name__)


        def migrate(cr, version):
            """Run the reviewed {stage}-migration hook."""
            if not version:
                return
            _logger.info("{spec.module_name} {stage}-migration from %s", version)
        '''
    )


def _render_odoo_tests(spec: BridgeSpec) -> str:
    if spec.bridge_mode == "odoo_native_extension":
        return _render_native_odoo_tests(spec)

    action = spec.actions[0]
    first_field = spec.fields[0]

    def proof_value(field: BridgeFieldSpec) -> object:
        if field.field_type == "Boolean":
            return True
        if field.field_type in {"Integer", "Float"}:
            return 7
        if field.field_type == "Json":
            return {"kind": "proof"}
        if field.field_type in {"Date", "Datetime"}:
            return "2026-07-23" if field.field_type == "Date" else "2026-07-23 12:00:00"
        return "Proof"

    test_values = {field.name: proof_value(field) for field in spec.fields if field.required or field == first_field}
    class_name = f"Test{_class_name(spec.domain)}Bridge"
    return _clean(
        f'''
        """Generated Odoo transaction tests for the bridge protocol."""

        from datetime import timedelta
        from unittest.mock import patch

        from odoo import fields
        from odoo.exceptions import AccessError, UserError, ValidationError
        from odoo.tests import TransactionCase, tagged


        @tagged("standard", "at_install")
        class {class_name}(TransactionCase):
            @classmethod
            def setUpClass(cls):
                super().setUpClass()
                bridge_user = cls.env["res.users"].with_context(no_reset_password=True).create(
                    {{
                        "name": "FMOS Bridge Test Operator",
                        "login": "fmos-bridge-test-operator@example.test",
                        "company_id": cls.env.company.id,
                        "company_ids": [(6, 0, [cls.env.company.id])],
                        "group_ids": [
                            (
                                6,
                                0,
                                [
                                    cls.env.ref("base.group_user").id,
                                    cls.env.ref(
                                        {f"{spec.module_name}.group_bridge_admin"!r}
                                    ).id,
                                ],
                            )
                        ],
                    }}
                )
                cls.Bridge = cls.env[{spec.model_name!r}].with_user(bridge_user)
                cls.Receipt = cls.env[{_receipt_model(spec)!r}].with_user(bridge_user)
                cls.Outbox = cls.env[{_queue_model(spec)!r}].with_user(bridge_user)
                cls.values = {test_values!r}
                cls.canonical_id = "fam-proof-record-0001"
                cls.correlation_id = "fam-proof-correlation-0001"
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

            def _project(self, version=1, values=None, authority=None, canonical_id=None):
                values = values or self.values
                authority = authority or self.authority
                canonical_id = canonical_id or self.canonical_id
                fingerprint = self.Bridge._projection_fingerprint(
                    canonical_id,
                    version,
                    authority,
                    values,
                )
                return self.Bridge.apply_fmos_projection(
                    authority_scope=authority,
                    canonical_id=canonical_id,
                    canonical_version=version,
                    fingerprint=fingerprint,
                    values=values,
                    origin="fmos",
                    correlation_id=self.correlation_id,
                )

            def test_projection_is_idempotent_and_monotonic(self):
                first = self._project()
                duplicate = self._project()
                self.assertEqual(first, duplicate)
                with self.assertRaises(ValidationError):
                    self._project(
                        version=1,
                        values=dict(self.values, **{{{first_field.name!r}: "conflict"}}),
                    )
                second = self._project(version=2)
                self.assertEqual(second.canonical_version, 2)
                with self.assertRaises(ValidationError):
                    self._project(version=1)

            def test_projection_takes_transaction_advisory_lock(self):
                with patch.object(type(self.Bridge), "_acquire_projection_lock", autospec=True) as acquire:
                    self._project(canonical_id="fam-lock-proof-0001")
                acquire.assert_called_once()

            def test_action_allowlist_and_atomic_outbox(self):
                bridge = self._project()
                with patch.object(type(self.Receipt), "_acquire_idempotency_lock", autospec=True) as acquire:
                    receipt = bridge.action_{action.name}(
                        idempotency_key="fam-action-proof-0001",
                        correlation_id="fam-action-correlation-0001",
                        expected_canonical_version=bridge.canonical_version,
                        payload={{"proof": True}},
                    )
                acquire.assert_called_once()
                self.assertEqual(
                    acquire.call_args.args[1:],
                    (bridge.company_id.id, "fam-action-proof-0001"),
                )
                self.assertEqual(receipt.action, {action.name!r})
                self.assertEqual(receipt.aggregate_record_id, bridge.id)
                self.assertEqual(receipt.aggregate_canonical_id, bridge.canonical_id)
                self.assertEqual(receipt.expected_canonical_version, bridge.canonical_version)
                self.assertEqual(receipt.aggregate_fingerprint, bridge.fingerprint)
                self.assertEqual(self.Outbox.search_count([("receipt_id", "=", receipt.id)]), 1)
                command = self.Outbox.search([("receipt_id", "=", receipt.id)])
                for field_name, value in self.authority.items():
                    actual = command.company_id.id if field_name == "company_id" else command[field_name]
                    self.assertEqual(actual, value)
                duplicate = bridge.action_{action.name}(
                    idempotency_key="fam-action-proof-0001",
                    correlation_id="fam-action-correlation-0001",
                    expected_canonical_version=bridge.canonical_version,
                    payload={{"proof": True}},
                )
                self.assertEqual(receipt, duplicate)
                other = self._project(canonical_id="fam-other-record-0001")
                with self.assertRaises(ValidationError):
                    other.action_{action.name}(
                        idempotency_key="fam-action-proof-0001",
                        correlation_id="fam-action-correlation-0001",
                        expected_canonical_version=other.canonical_version,
                        payload={{"proof": True}},
                    )
                bridge = self._project(version=2)
                with self.assertRaises(ValidationError):
                    bridge.action_{action.name}(
                        idempotency_key="fam-action-proof-0001",
                        correlation_id="fam-action-correlation-0001",
                        expected_canonical_version=2,
                        payload={{"proof": True}},
                    )

            def test_full_authority_scope_isolated_and_immutable(self):
                other = self.env["res.company"].create({{"name": "Bridge Isolation Proof"}})
                scoped = self.Bridge.with_context(allowed_company_ids=[self.env.company.id])
                values = self.values
                other_company_scope = dict(self.authority, company_id=other.id)
                fingerprint = scoped._projection_fingerprint(
                    self.canonical_id,
                    1,
                    other_company_scope,
                    values,
                )
                with self.assertRaises(AccessError):
                    scoped.apply_fmos_projection(
                        authority_scope=other_company_scope,
                        canonical_id=self.canonical_id,
                        canonical_version=1,
                        fingerprint=fingerprint,
                        values=values,
                        origin="fmos",
                        correlation_id=self.correlation_id,
                    )
                first = self._project()
                for field_name in (
                    "network_id",
                    "business_id",
                    "app_id",
                    "channel_id",
                    "identity_id",
                    "actor_id",
                    "install_id",
                ):
                    changed_value = f"{{field_name}}-proof-0002"
                    changed_scope = dict(self.authority, **{{field_name: changed_value}})
                    isolated = self._project(authority=changed_scope)
                    self.assertNotEqual(first.id, isolated.id)
                    self.assertEqual(isolated[field_name], changed_value)
                    self.assertEqual(first[field_name], self.authority[field_name])
                    with self.assertRaises(ValidationError):
                        first.sudo().write({{field_name: changed_value}})

            def test_reconciliation_observation_is_persisted(self):
                bridge = self._project()
                destination_fingerprint = "0" * 64
                observed = self.Bridge.apply_reconciliation_observation(
                    authority_scope=self.authority,
                    canonical_id=self.canonical_id,
                    source_version=bridge.canonical_version,
                    source_fingerprint=bridge.fingerprint,
                    destination_version=0,
                    destination_fingerprint=destination_fingerprint,
                    correlation_id="fam-reconcile-proof-0001",
                )
                self.assertEqual(observed.reconciliation_state, "diverged")
                self.assertEqual(observed.source_version, 1)
                self.assertEqual(observed.destination_version, 0)
                self.assertEqual(observed.reconciliation_lag, 1)
                self.assertTrue(observed.last_reconciled_at)

            def test_bridge_ledgers_are_read_only_over_rpc(self):
                for model_name in ({spec.model_name!r}, {_receipt_model(spec)!r}, {_queue_model(spec)!r}):
                    access_rows = self.env["ir.model.access"].search(
                        [
                            ("model_id.model", "=", model_name),
                            (
                                "group_id",
                                "in",
                                (
                                    self.env.ref({f"{spec.module_name}.group_bridge_operator"!r}).id,
                                    self.env.ref({f"{spec.module_name}.group_bridge_admin"!r}).id,
                                ),
                            ),
                        ]
                    )
                    self.assertTrue(access_rows)
                    self.assertTrue(all(access_rows.mapped("perm_read")))
                    self.assertFalse(any(access_rows.mapped("perm_write")))
                    self.assertFalse(any(access_rows.mapped("perm_create")))
                    self.assertFalse(any(access_rows.mapped("perm_unlink")))

            def test_retry_dead_letter_and_recovery(self):
                bridge = self._project()
                receipt = bridge.action_{action.name}(
                    idempotency_key="fam-retry-proof-0001",
                    correlation_id="fam-retry-correlation-0001",
                    expected_canonical_version=bridge.canonical_version,
                    payload={{"proof": True}},
                )
                command = self.Outbox.search([("receipt_id", "=", receipt.id)])
                command.sudo().write({{"max_attempts": 1}})
                claimed = self.Outbox._claim_due_commands()
                self.assertIn(command, claimed)
                self.assertEqual(command.state, "processing")
                self.assertTrue(command.processing_lease_expires_at)
                first_processing_token = command.processing_token
                self.assertTrue(first_processing_token)
                self.assertFalse(command.next_attempt_at)
                with patch.object(type(command), "_deliver_command", side_effect=UserError("proof failure")):
                    self.assertFalse(command._process_one())
                self.assertEqual(command.state, "dead_letter")
                self.assertEqual(command.last_error_code, "UserError")
                self.assertFalse(command.next_attempt_at)
                self.assertFalse(command.processing_lease_expires_at)
                self.assertEqual(bridge.reconciliation_dead_letter_count, 1)
                command.action_recover()
                self.assertEqual(command.state, "retry")
                self.assertEqual(command.attempts, 0)
                self.assertTrue(command.next_attempt_at)
                self.assertEqual(bridge.reconciliation_state, "pending")
                command.sudo().write(
                    {{
                        "state": "processing",
                        "next_attempt_at": False,
                        "processing_started_at": fields.Datetime.now() - timedelta(seconds=901),
                        "processing_lease_expires_at": fields.Datetime.now() - timedelta(seconds=1),
                        "processing_token": first_processing_token,
                    }}
                )
                reclaimed = self.Outbox._claim_due_commands()
                self.assertIn(command, reclaimed)
                self.assertGreater(command.processing_lease_expires_at, fields.Datetime.now())
                self.assertNotEqual(command.processing_token, first_processing_token)
                current_processing_token = command.processing_token
                self.assertFalse(command._process_one(first_processing_token))
                self.assertEqual(command.state, "processing")
                self.assertEqual(command.processing_token, current_processing_token)
        '''
    )


def _render_native_odoo_tests(spec: BridgeSpec) -> str:
    """Render runtime ownership tests for an Odoo-native extension."""
    action = spec.actions[0]
    native_manager_group = {
        "account.move": "account.group_account_manager",
        "event.registration": "event.group_event_manager",
        "helpdesk.ticket": "helpdesk.group_helpdesk_manager",
        "project.project": "project.group_project_manager",
        "project.task": "project.group_project_manager",
        "sale.order": "sales_team.group_sale_manager",
    }[spec.model_name]
    if spec.model_name == "sale.order":
        setup = """
        partner = self.env["res.partner"].create({"name": "Native Bridge Proof Partner"})
        return self.Native.create({"partner_id": partner.id})
        """
    elif spec.model_name == "project.task":
        setup = """
        project = self.env["project.project"].create({"name": "Native Bridge Proof Project"})
        return self.Native.create({"name": "Native Bridge Proof Task", "project_id": project.id})
        """
    elif spec.model_name == "account.move":
        setup = 'return self.Native.create({"move_type": "entry"})'
    elif spec.model_name == "event.registration":
        setup = """
        event = self.env["event.event"].create({"name": "Native Bridge Proof Event"})
        return self.Native.create({"name": "Native Bridge Proof Registration", "event_id": event.id})
        """
    else:
        setup = 'return self.Native.create({"name": "Native Bridge Proof"})'
    return _clean(
        f'''
        """Generated Odoo runtime tests for native aggregate ownership."""

        from datetime import timedelta
        from unittest.mock import patch

        from odoo import fields
        from odoo.exceptions import UserError, ValidationError
        from odoo.tests import TransactionCase, tagged


        @tagged("standard", "at_install")
        class Test{_class_name(spec.domain)}NativeBridge(TransactionCase):
            @classmethod
            def setUpClass(cls):
                super().setUpClass()
                bridge_user = cls.env["res.users"].with_context(no_reset_password=True).create(
                    {{
                        "name": "Native Bridge Test Operator",
                        "login": "native-bridge-test-operator@example.test",
                        "company_id": cls.env.company.id,
                        "company_ids": [(6, 0, [cls.env.company.id])],
                        "group_ids": [
                            (
                                6,
                                0,
                                [
                                    cls.env.ref("base.group_user").id,
                                    cls.env.ref({native_manager_group!r}).id,
                                    cls.env.ref({f"{spec.module_name}.group_bridge_admin"!r}).id,
                                ],
                            )
                        ],
                    }}
                )
                cls.Native = cls.env[{spec.model_name!r}].with_user(bridge_user)
                cls.Receipt = cls.env[{_receipt_model(spec)!r}].with_user(bridge_user)
                cls.Outbox = cls.env[{_queue_model(spec)!r}].with_user(bridge_user)

            def _create_native_record(self):
        {textwrap.indent(textwrap.dedent(setup).strip(), "        ")}

            def _authority_scope(self, record):
                company = record.company_id or self.env.company
                return {{
                    "network_id": "network-proof-01",
                    "business_id": "business-proof-01",
                    "app_id": "application-proof-01",
                    "channel_id": "dashboard-proof-01",
                    "identity_id": "identity-proof-01",
                    "actor_id": "operator-proof-01",
                    "company_id": company.id,
                    "install_id": "odoo-install-proof-01",
                }}

            def test_ordinary_native_create_remains_valid(self):
                record = self._create_native_record()
                self.assertTrue(record.exists())
                self.assertNotIn("network_id", record._fields)
                self.assertNotIn("canonical_id", record._fields)

            def test_native_extension_has_no_fmos_projection_path(self):
                record = self._create_native_record()
                self.assertFalse(hasattr(record, "apply_fmos_projection"))
                self.assertNotIn("apply_fmos_projection", type(record).__dict__)

            def test_fixed_native_event_is_snapshotted_and_queued(self):
                record = self._create_native_record()
                authority_scope = self._authority_scope(record)
                receipt = record.action_{action.name}(
                    authority_scope=authority_scope,
                    idempotency_key="native-action-proof-0001",
                    correlation_id="native-correlation-proof-0001",
                    payload={{"proof": True}},
                )
                self.assertEqual(receipt.source_model, {spec.model_name!r})
                self.assertEqual(receipt.source_record_id, record.id)
                expected_company = record.company_id or self.env.company
                self.assertEqual(receipt.source_snapshot["company_id"], expected_company.id)
                self.assertEqual(
                    receipt.source_snapshot["company_scope"],
                    "source" if record.company_id else "execution_context",
                )
                for field_name, value in authority_scope.items():
                    actual = getattr(receipt, field_name)
                    self.assertEqual(actual.id if field_name == "company_id" else actual, value)
                outbox = self.Outbox.search([("receipt_id", "=", receipt.id)])
                self.assertEqual(len(outbox), 1)
                for field_name, value in authority_scope.items():
                    actual = getattr(outbox, field_name)
                    self.assertEqual(actual.id if field_name == "company_id" else actual, value)
                duplicate = record.action_{action.name}(
                    authority_scope=authority_scope,
                    idempotency_key="native-action-proof-0001",
                    correlation_id="native-correlation-proof-0001",
                    payload={{"proof": True}},
                )
                self.assertEqual(receipt, duplicate)

            def test_native_outbox_retry_uses_claim_and_lease_recovery(self):
                record = self._create_native_record()
                receipt = record.action_{action.name}(
                    authority_scope=self._authority_scope(record),
                    idempotency_key="native-retry-proof-0001",
                    correlation_id="native-retry-correlation-0001",
                    payload={{"proof": True}},
                )
                command = self.Outbox.search([("receipt_id", "=", receipt.id)])
                command.sudo().write({{"max_attempts": 1}})
                claimed = self.Outbox._claim_due_commands()
                self.assertIn(command, claimed)
                first_processing_token = command.processing_token
                self.assertTrue(first_processing_token)
                with patch.object(type(command), "_deliver_command", side_effect=UserError("proof failure")):
                    self.assertFalse(command._process_one())
                self.assertEqual(command.state, "dead_letter")
                self.assertFalse(command.next_attempt_at)
                command.action_recover()
                self.assertEqual(command.state, "retry")
                self.assertTrue(command.next_attempt_at)
                command.sudo().write(
                    {{
                        "state": "processing",
                        "next_attempt_at": False,
                        "processing_started_at": fields.Datetime.now() - timedelta(seconds=901),
                        "processing_lease_expires_at": fields.Datetime.now() - timedelta(seconds=1),
                        "processing_token": first_processing_token,
                    }}
                )
                reclaimed = self.Outbox._claim_due_commands()
                self.assertIn(command, reclaimed)
                self.assertNotEqual(command.processing_token, first_processing_token)
                current_processing_token = command.processing_token
                self.assertFalse(command._process_one(first_processing_token))
                self.assertEqual(command.state, "processing")
                self.assertEqual(command.processing_token, current_processing_token)
        '''
    )


def _render_pot(spec: BridgeSpec) -> str:
    labels = {
        spec.module_title,
        "Action Receipts",
        "Bridge Administrator",
        "Bridge Operator",
        "Canonical Records",
        "Command Outbox",
        "Recover",
        "Request Reconciliation",
    }
    labels.update(field.label for field in spec.fields)
    labels.update(action.label for action in spec.actions)
    entries = "\n\n".join(f'msgid {label!r}\nmsgstr ""' for label in sorted(labels))
    return _clean(
        f"""
        # Translation template for {spec.module_name}.
        msgid ""
        msgstr ""
        "Project-Id-Version: {spec.version}\\n"
        "Content-Type: text/plain; charset=UTF-8\\n"
        "Content-Transfer-Encoding: 8bit\\n"

        {entries}
        """
    )


def _render_readme(spec: BridgeSpec) -> str:
    return _clean(
        f"""
        {spec.module_title}
        {"=" * len(spec.module_title)}

        Generated Odoo 19 seam for the ``{spec.domain}`` FMOS domain.

        * authoritative writer: {"FMOS" if spec.bridge_mode == "fmos_authoritative" else "Odoo"}
        * aggregate model: ``{spec.model_name}``
        * projection protocol: monotonic version + canonical SHA-256 fingerprint
        * reverse protocol: immutable action receipt + transactional command outbox
        * isolation: required ``company_id`` and global company record rules
        * recovery: exponential retry, dead-letter state, admin recovery, reconciliation command

        Delivery is deliberately an adapter hook. This addon never selects a model,
        method, or URL from runtime input.
        """
    )


def render_files(spec: BridgeSpec) -> dict[str, str]:
    """Render every canonical addon file for a validated spec."""
    version_dir = spec.version
    return {
        "INSTALL_CONTRACT.json": _render_install_contract(spec),
        "README.rst": _render_readme(spec),
        "__init__.py": _render_root_init(),
        "__manifest__.py": _render_manifest(spec),
        "data/cron.xml": _render_cron(spec),
        "hooks.py": _render_hooks(spec),
        f"i18n/{spec.module_name}.pot": _render_pot(spec),
        f"migrations/{version_dir}/post-migration.py": _render_migration("post", spec),
        f"migrations/{version_dir}/pre-migration.py": _render_migration("pre", spec),
        "models/__init__.py": _render_models_init(spec),
        "models/action_receipt.py": _render_receipt_model(spec),
        f"models/{spec.domain}_bridge.py": _render_bridge_model(spec),
        "models/command_outbox.py": _render_outbox_model(spec),
        "security/groups.xml": _render_groups(spec),
        "security/ir.model.access.csv": _render_access_csv(spec),
        "security/record_rules.xml": _render_record_rules(spec),
        "tests/__init__.py": "from . import test_bridge\n",
        "tests/test_bridge.py": _render_odoo_tests(spec),
        "views/action_receipt_views.xml": _render_receipt_views(spec),
        "views/bridge_record_views.xml": _render_bridge_views(spec),
        "views/command_outbox_views.xml": _render_outbox_views(spec),
        "views/menu.xml": _render_menu(spec),
    }
