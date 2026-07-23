"""Immutable Odoo action receipts created atomically with command outbox rows."""

import hashlib
import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_ALLOWED_ACTIONS = frozenset(('request_download', 'request_share', 'archive', 'reconcile'))


class FamActionReceipt(models.Model):
    _name = 'fm.fam.action.receipt'
    _description = 'FMOS FAM Bridge Action Receipt'
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
    fam_id = fields.Many2one(
        'fm.fam.asset',
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
    action = fields.Selection((('request_download', 'Request Download'), ('request_share', 'Request Share'), ('archive', 'Archive'), ('reconcile', 'Reconcile')), required=True, index=True)
    action_contract_id = fields.Char(required=True, readonly=True, default='fam.action.v1')
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
        material = f"{company_id}:{idempotency_key}".encode()
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
        authority_scope = {
            field_name: getattr(aggregate, field_name)
            for field_name in ('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'install_id')
        }
        authority_scope["company_id"] = aggregate.company_id.id
        try:
            encoded = json.dumps(
                {
                    "company_id": aggregate.company_id.id,
                    "aggregate_record_id": aggregate.id,
                    "aggregate_canonical_id": aggregate.canonical_id,
                    "expected_canonical_version": expected_canonical_version,
                    "aggregate_fingerprint": aggregate.fingerprint,
                    "authority_scope": authority_scope,
                    "action": action,
                    "action_contract_id": 'fam.action.v1',
                    "command_type": command_type,
                    "payload": payload,
                },
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

        scope_values = {
            field_name: getattr(aggregate, field_name)
            for field_name in ('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'install_id')
        }
        receipt = self.sudo().create(
            {
                "company_id": aggregate.company_id.id,
                **scope_values,
                'fam_id': aggregate.id,
                "aggregate_record_id": aggregate.id,
                "aggregate_canonical_id": aggregate.canonical_id,
                "expected_canonical_version": expected_canonical_version,
                "aggregate_fingerprint": aggregate.fingerprint,
                "action": action,
                "action_contract_id": 'fam.action.v1',
                "command_type": command_type,
                "idempotency_key": idempotency_key,
                "payload": payload,
                "semantic_fingerprint": fingerprint,
                "correlation_id": correlation_id,
                "causation_id": aggregate.correlation_id,
            }
        )
        self.env['fm.fam.command.outbox'].sudo()._enqueue_receipt(receipt)
        return receipt
