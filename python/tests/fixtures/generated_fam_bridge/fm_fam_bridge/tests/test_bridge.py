"""Generated Odoo transaction tests for the bridge protocol."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFamBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        bridge_user = cls.env["res.users"].with_context(no_reset_password=True).create(
            {
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
                                'fm_fam_bridge.group_bridge_admin'
                            ).id,
                        ],
                    )
                ],
            }
        )
        cls.Bridge = cls.env['fm.fam.asset'].with_user(bridge_user)
        cls.Receipt = cls.env['fm.fam.action.receipt'].with_user(bridge_user)
        cls.Outbox = cls.env['fm.fam.command.outbox'].with_user(bridge_user)
        cls.values = {'name': 'Proof', 'asset_kind': 'Proof', 'status': 'Proof'}
        cls.canonical_id = "fam-proof-record-0001"
        cls.correlation_id = "fam-proof-correlation-0001"
        cls.authority = {
            "network_id": "network-proof-0001",
            "business_id": "business-proof-0001",
            "app_id": "application-proof-0001",
            "channel_id": "channel-proof-0001",
            "identity_id": "identity-proof-0001",
            "actor_id": "actor-proof-0001",
            "company_id": cls.env.company.id,
            "install_id": "install-proof-0001",
        }

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
                values=dict(self.values, **{'name': "conflict"}),
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
            receipt = bridge.action_request_download(
                idempotency_key="fam-action-proof-0001",
                correlation_id="fam-action-correlation-0001",
                expected_canonical_version=bridge.canonical_version,
                payload={"proof": True},
            )
        acquire.assert_called_once()
        self.assertEqual(
            acquire.call_args.args[1:],
            (bridge.company_id.id, "fam-action-proof-0001"),
        )
        self.assertEqual(receipt.action, 'request_download')
        self.assertEqual(receipt.aggregate_record_id, bridge.id)
        self.assertEqual(receipt.aggregate_canonical_id, bridge.canonical_id)
        self.assertEqual(receipt.expected_canonical_version, bridge.canonical_version)
        self.assertEqual(receipt.aggregate_fingerprint, bridge.fingerprint)
        self.assertEqual(self.Outbox.search_count([("receipt_id", "=", receipt.id)]), 1)
        command = self.Outbox.search([("receipt_id", "=", receipt.id)])
        for field_name, value in self.authority.items():
            actual = command.company_id.id if field_name == "company_id" else command[field_name]
            self.assertEqual(actual, value)
        duplicate = bridge.action_request_download(
            idempotency_key="fam-action-proof-0001",
            correlation_id="fam-action-correlation-0001",
            expected_canonical_version=bridge.canonical_version,
            payload={"proof": True},
        )
        self.assertEqual(receipt, duplicate)
        other = self._project(canonical_id="fam-other-record-0001")
        with self.assertRaises(ValidationError):
            other.action_request_download(
                idempotency_key="fam-action-proof-0001",
                correlation_id="fam-action-correlation-0001",
                expected_canonical_version=other.canonical_version,
                payload={"proof": True},
            )
        bridge = self._project(version=2)
        with self.assertRaises(ValidationError):
            bridge.action_request_download(
                idempotency_key="fam-action-proof-0001",
                correlation_id="fam-action-correlation-0001",
                expected_canonical_version=2,
                payload={"proof": True},
            )

    def test_full_authority_scope_isolated_and_immutable(self):
        other = self.env["res.company"].create({"name": "Bridge Isolation Proof"})
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
            changed_value = f"{field_name}-proof-0002"
            changed_scope = dict(self.authority, **{field_name: changed_value})
            isolated = self._project(authority=changed_scope)
            self.assertNotEqual(first.id, isolated.id)
            self.assertEqual(isolated[field_name], changed_value)
            self.assertEqual(first[field_name], self.authority[field_name])
            with self.assertRaises(ValidationError):
                first.sudo().write({field_name: changed_value})

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
        for model_name in ('fm.fam.asset', 'fm.fam.action.receipt', 'fm.fam.command.outbox'):
            access_rows = self.env["ir.model.access"].search(
                [
                    ("model_id.model", "=", model_name),
                    (
                        "group_id",
                        "in",
                        (
                            self.env.ref('fm_fam_bridge.group_bridge_operator').id,
                            self.env.ref('fm_fam_bridge.group_bridge_admin').id,
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
        receipt = bridge.action_request_download(
            idempotency_key="fam-retry-proof-0001",
            correlation_id="fam-retry-correlation-0001",
            expected_canonical_version=bridge.canonical_version,
            payload={"proof": True},
        )
        command = self.Outbox.search([("receipt_id", "=", receipt.id)])
        command.sudo().write({"max_attempts": 1})
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
            {
                "state": "processing",
                "next_attempt_at": False,
                "processing_started_at": fields.Datetime.now() - timedelta(seconds=901),
                "processing_lease_expires_at": fields.Datetime.now() - timedelta(seconds=1),
                "processing_token": first_processing_token,
            }
        )
        reclaimed = self.Outbox._claim_due_commands()
        self.assertIn(command, reclaimed)
        self.assertGreater(command.processing_lease_expires_at, fields.Datetime.now())
        self.assertNotEqual(command.processing_token, first_processing_token)
        current_processing_token = command.processing_token
        self.assertFalse(command._process_one(first_processing_token))
        self.assertEqual(command.state, "processing")
        self.assertEqual(command.processing_token, current_processing_token)
