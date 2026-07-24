# FMOS-806 IncPros and Hub/Flex thin-wrapper parity

The declarations in `python/specs/fmos806/` are the canonical generator inputs.
`incpros-temp` is a vocabulary and behavior donor only. Generated addons have no
WordPress imports, endpoints, identifiers, payloads, or runtime dependency.

## Canonical addon and model names

| Addon | Models |
| --- | --- |
| `fm_incpros_base` | `fm.incpros.crosswalk`, `fm.incpros.projection.receipt`, `fm.incpros.action.receipt`, `fm.incpros.command.outbox` |
| `fm_incpros_entities` | `fm.incpros.entity` |
| `fm_incpros_intakes` | `fm.incpros.intake` |
| `fm_incpros_matters` | `fm.incpros.matter` |
| `fm_hub_flex` | `fm.hub.flex.profile`, `fm.hub.flex.earning`, `fm.hub.flex.nexus`, `fm.hub.flex.queue`, plus protocol models below `fm.hub.flex.*` |

The provisional `fm_incpros` addon and `fm.incpros.workflow` model are invalid.
Capability descriptors must select the modular addons and use
`fm.incpros.matter`.

## IncPros donor alignment

- Entities preserve `entity_form`, the nine-state entity lifecycle, intake/order
  references, and the computed next-action vocabulary. `wc_order_id` becomes the
  provider-neutral `commerce_order_canonical_id`.
- Intakes preserve Entity Establishment (`EE`), Professional Structure (`PS`),
  Organized Capital (`OC`), and Annual Compliance (`AC`) service-family terms,
  versioned intake state, and checkout progression. Intake has no donor business
  action: the live donor exposes no safe operator mutation independent of
  customer submission and payment. Its only Odoo command is protocol-level
  `request_reconciliation`.
- Matters replace the provisional “workflow” concept. They preserve product,
  subproduct, venture, acquisition vehicle, segment, route, intake/payment/state
  fee/review/release/provider statuses, complexity, formation, and the canonical
  matter lifecycle.
- Matter Odoo actions preserve these live command names:
  `approve_submission`, `resume_after_info`, `release_package`,
  `block_release`, `complete_matter`, and `reconcile_financials`.
- `request_info` is excluded because the live action requires operator-written
  free text. AI Lab actions, payment overrides, refunds, counsel/CPA escalation,
  signature controls, and manual provider advancement are excluded. AI Lab is
  the separate global `fm_ai_lab` addon.

No donor email, contact name, legal/trade name, address, EIN, Cognito subject,
intake payload, notes, evidence, provider token, or AI output is projected.

## Hub/Flex alignment

- Profile projections exclude display name, biography, avatar, person ID, and
  payment-provider account ID. `payment_onboarding_status` is the non-provider
  wrapper term for the donor's provider-specific onboarding state.
- Earnings preserve service outcome, cents-denominated amounts, wait time, hub,
  nexus, and completion time; customer identity is excluded.
- Nexus preserves canonical hub/station/profile IDs, tether method, lifecycle,
  immutable fee snapshot, and timing. GPS and payment account data are excluded.
- Queue preserves position, lifecycle, service type, outcome, assignments, and
  timing. Customer names, phones, email, contact IDs, kiosk tokens, and partition
  keys are excluded.
- Odoo actions are limited to `update_profile`, `end_nexus`, and `cancel_queue`.
  Tether/join require location or customer-presence data and remain in the
  headless FMOS experience. Administrative call-next/complete/remove, ghost
  controls, fee controls, and GPS bypass remain outside the wrapper.

## Protocol ownership

Every projection is keyed by the exact install/company/network/business/app/
channel/identity/actor authority scope and FMOS canonical ID. Projection and
action receipts are immutable. Same-version or same-idempotency duplicates are
accepted only when fingerprints match; stale versions and fingerprint conflicts
fail closed. FMOS/reconciler are the only accepted projection origins, and
projection application never emits a reverse command. Odoo actions create an
immutable receipt and transactional command outbox row for a compile-time
allowlisted canonical FMOS command type.
