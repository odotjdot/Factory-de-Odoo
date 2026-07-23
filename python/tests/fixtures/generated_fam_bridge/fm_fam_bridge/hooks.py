"""Install-time contract verification for fm_fam_bridge."""

from odoo.exceptions import UserError

_REQUIRED_MODELS = ('fm.fam.asset', 'fm.fam.action.receipt', 'fm.fam.command.outbox')
_REQUIRED_FIELDS = ('network_id', 'business_id', 'app_id', 'channel_id', 'identity_id', 'actor_id', 'company_id', 'install_id', 'canonical_id', 'canonical_version', 'fingerprint', 'origin', 'correlation_id', 'causation_id', 'last_projection_at', 'reconciliation_state', 'source_version', 'destination_version', 'source_fingerprint', 'destination_fingerprint', 'last_reconciled_at', 'reconciliation_lag')


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
    missing_fields = [
        field_name
        for field_name in _REQUIRED_FIELDS
        if field_name not in env['fm.fam.asset']._fields
    ]
    if missing_fields:
        raise UserError(
            env._(
                "Bridge installation is incomplete; missing fields: %(fields)s",
                fields=", ".join(missing_fields),
            )
        )
