"""Retryable transactional command outbox with dead-letter recovery."""

import logging
import uuid
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
_ALLOWED_COMMAND_TYPES = frozenset(('fam.download.requested.v1', 'fam.share.requested.v1', 'fam.archive.requested.v1', 'fam.reconcile.requested.v1'))
_MAX_ATTEMPTS = 8
_RETRY_BASE_SECONDS = 60
_RETRY_CAP_SECONDS = 3600
_CRON_BATCH_SIZE = 100
_PROCESSING_LEASE_SECONDS = 900


class FamCommandOutbox(models.Model):
    _name = 'fm.fam.command.outbox'
    _description = 'FMOS FAM Bridge Command Outbox'
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
        'fm.fam.action.receipt',
        required=True,
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    fam_id = fields.Many2one(
        'fm.fam.asset',
        related='receipt_id.fam_id',
        store=True,
        check_company=True,
        index=True,
    )
    aggregate_record_id = fields.Integer(required=True, readonly=True, index=True)
    aggregate_canonical_id = fields.Char(required=True, readonly=True, index=True)
    expected_canonical_version = fields.Integer(required=True, readonly=True)
    aggregate_fingerprint = fields.Char(required=True, readonly=True)
    semantic_fingerprint = fields.Char(required=True, readonly=True)
    action_contract_id = fields.Char(required=True, readonly=True, default='fam.action.v1')
    command_type = fields.Selection((('fam.download.requested.v1', 'fam.download.requested.v1'), ('fam.share.requested.v1', 'fam.share.requested.v1'), ('fam.archive.requested.v1', 'fam.archive.requested.v1'), ('fam.reconcile.requested.v1', 'fam.reconcile.requested.v1')), required=True, readonly=True, index=True)
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
        scope_values = {
            field_name: getattr(receipt, field_name)
            for field_name in ('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'install_id')
        }
        return self.sudo().create(
            {
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
            }
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
        values = {
            "attempts": attempts,
            "last_attempt_at": fields.Datetime.now(),
            "last_error_code": type(error).__name__[:120],
            "processing_started_at": False,
            "processing_lease_expires_at": False,
            "processing_token": False,
        }
        if attempts >= self.max_attempts:
            values.update(
                {
                    "state": "dead_letter",
                    "next_attempt_at": False,
                    "dead_lettered_at": fields.Datetime.now(),
                }
            )
        else:
            values.update(
                {
                    "state": "retry",
                    "next_attempt_at": fields.Datetime.now()
                    + timedelta(seconds=self._retry_delay_seconds(attempts)),
                }
            )
        self.sudo().write(values)
        aggregate = self.fam_id.sudo()
        aggregate_values = {
            "reconciliation_state": "error",
            "last_reconciliation_error_code": type(error).__name__[:120],
            "reconciliation_retry_count": aggregate.reconciliation_retry_count + 1,
        }
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
            FROM "{self._table}"
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
            {
                "state": "done",
                "attempts": self.attempts + 1,
                "delivered_at": delivered_at,
                "dispatch_lag_seconds": dispatch_lag_seconds,
                "last_error_code": False,
                "next_attempt_at": False,
                "processing_started_at": False,
                "processing_lease_expires_at": False,
                "processing_token": False,
            }
        )
        self.receipt_id.sudo().write({"state": "dispatched"})
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
                FROM "{self._table}"
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
            UPDATE "{self._table}" AS command
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
        if not self.env.user.has_group('fm_fam_bridge.group_bridge_admin'):
            raise UserError(self.env._("Bridge administrator access is required."))
        recoverable = self.filtered(lambda command: command.state == "dead_letter")
        recoverable.sudo().write(
            {
                "state": "retry",
                "attempts": 0,
                "next_attempt_at": fields.Datetime.now(),
                "last_error_code": False,
                "dead_lettered_at": False,
                "processing_started_at": False,
                "processing_lease_expires_at": False,
                "processing_token": False,
            }
        )
        recoverable.mapped('fam_id').sudo().write(
            {
                "reconciliation_state": "pending",
                "last_reconciliation_error_code": False,
            }
        )
        return True
