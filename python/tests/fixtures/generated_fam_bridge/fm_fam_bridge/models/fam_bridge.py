"""Canonical FMOS projection and fixed Odoo action surface."""

import hashlib
import json
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

_ALLOWED_PROJECTION_FIELDS = frozenset(('name', 'asset_kind', 'status', 'metadata'))
_ALLOWED_ACTIONS = frozenset(('request_download', 'request_share', 'archive', 'reconcile'))
_AUTHORITY_FIELDS = frozenset(('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'company_id', 'install_id'))
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")


class FamBridge(models.Model):
    _name = 'fm.fam.asset'
    _description = 'FMOS FAM Bridge'
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
    origin = fields.Selection((('fmos', 'FMOS'), ('odoo', 'Odoo'), ('reconciler', 'Reconciler')), string="Change Origin", required=True, copy=False)
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
    name = fields.Char(string='Asset Name', required=True, index=True)
    asset_kind = fields.Char(string='Asset Kind', required=True, index=True)
    status = fields.Char(string='Status', required=True, index=True)
    metadata = fields.Json(string='Metadata')

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
        if not self.env.user.has_group('fm_fam_bridge.group_bridge_operator'):
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
        normalized = {"company_id": company_id}
        for field_name in sorted(_AUTHORITY_FIELDS - {"company_id"}):
            normalized[field_name] = self._validate_identifier(
                authority_scope[field_name],
                field_name,
            )
        return normalized

    @api.model
    def _authority_domain(self, authority_scope):
        return [
            (field_name, "=", authority_scope[field_name])
            for field_name in ('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'company_id', 'install_id')
        ]

    @api.model
    def _projection_lock_keys(self, authority_scope, canonical_id):
        material = json.dumps(
            {
                "authority_scope": authority_scope,
                "canonical_id": canonical_id,
            },
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
        body = {
            "authority_scope": authority_scope,
            "canonical_id": canonical_id,
            "canonical_version": canonical_version,
            "values": values,
        }
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
            {
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
            }
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
        return self.env['fm.fam.action.receipt'].sudo()._capture_action(
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
            {
                "reconciliation_state": "in_sync" if in_sync else "diverged",
                "source_version": source_version,
                "destination_version": destination_version,
                "source_fingerprint": source_fingerprint,
                "destination_fingerprint": destination_fingerprint,
                "last_reconciled_at": fields.Datetime.now(),
                "last_reconciliation_error_code": False,
                "reconciliation_lag": abs(source_version - destination_version),
                "correlation_id": correlation_id,
            }
        )
        return record

    def action_request_download(
        self,
        *,
        idempotency_key,
        correlation_id,
        expected_canonical_version,
        payload=None,
    ):
        """Capture the allowlisted Request Download command."""
        return self._capture_allowlisted_action(
            'request_download',
            'fam.download.requested.v1',
            idempotency_key,
            payload or {},
            correlation_id,
            expected_canonical_version,
        )

    def action_request_share(
        self,
        *,
        idempotency_key,
        correlation_id,
        expected_canonical_version,
        payload=None,
    ):
        """Capture the allowlisted Request Share command."""
        return self._capture_allowlisted_action(
            'request_share',
            'fam.share.requested.v1',
            idempotency_key,
            payload or {},
            correlation_id,
            expected_canonical_version,
        )

    def action_archive(
        self,
        *,
        idempotency_key,
        correlation_id,
        expected_canonical_version,
        payload=None,
    ):
        """Capture the allowlisted Archive command."""
        return self._capture_allowlisted_action(
            'archive',
            'fam.archive.requested.v1',
            idempotency_key,
            payload or {},
            correlation_id,
            expected_canonical_version,
        )

    def action_request_reconciliation(self):
        """Enqueue a deterministic reconciliation command for this version."""
        self.ensure_one()
        self.sudo().write({"reconciliation_state": "pending"})
        idempotency_key = (
            f"reconcile:{self.canonical_id}:{self.canonical_version}:{self.fingerprint[:16]}"
        )
        return self._capture_allowlisted_action(
            "reconcile",
            'fam.reconcile.requested.v1',
            idempotency_key,
            {
                "canonical_id": self.canonical_id,
                "canonical_version": self.canonical_version,
                "fingerprint": self.fingerprint,
            },
            idempotency_key,
            self.canonical_version,
        )
