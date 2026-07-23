FMOS FAM Bridge
===============

Generated Odoo 19 seam for the ``fam`` FMOS domain.

* authoritative writer: FMOS
* aggregate model: ``fm.fam.asset``
* projection protocol: monotonic version + canonical SHA-256 fingerprint
* reverse protocol: immutable action receipt + transactional command outbox
* isolation: required ``company_id`` and global company record rules
* recovery: exponential retry, dead-letter state, admin recovery, reconciliation command

Delivery is deliberately an adapter hook. This addon never selects a model,
method, or URL from runtime input.
