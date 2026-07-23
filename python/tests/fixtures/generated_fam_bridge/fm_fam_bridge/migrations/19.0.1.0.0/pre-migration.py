"""Pre-migration skeleton for fm_fam_bridge 19.0.1.0.0."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Run the reviewed pre-migration hook."""
    if not version:
        return
    _logger.info("fm_fam_bridge pre-migration from %s", version)
