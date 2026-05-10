# ADR 0005 — Per-user entity ownership for Cocarte

**Status:** Accepted
**Date:** 2026-05-10
**Deciders:** GISPulse maintainers
**Issue:** [imagodata/gispulse-portal#56](https://github.com/imagodata/gispulse-portal/issues/56)

## Context

Cocarte (v1.7 "Publish") introduces user-facing maps that are first-class,
slug-addressable, and shareable to the public. The product wedge requires
that each user has an independent quota ("5 maps free per user", per the
v1.7 plan §2.4), revocable share links, and ownership-scoped access checks.

Until v1.7, every persisted entity in GISPulse — `Project`, `Trigger`,
`Rule`, `Scenario`, `Dataset`, `TableRelation` — has been **single-tenant
at the instance level**. The relevant tier check (`_PROJECT_LIMITS`) counts
rows globally; there is no `owner_id` column on any of them. This matches
the v1.5/v1.6 deployment model where one engine instance equals one user
or one organisation.

Cocarte breaks that assumption. A multi-user portal where Alice and Bob
each get five free maps cannot rely on global counts.

## Decision

`CocarteMap` is the **first GISPulse domain entity with a `owner_id`
column** that participates in the tier check. The convention introduced
here is the template for every future user-facing entity (mini-apps in
v1.8, comments and collaborator records in v1.9, gallery likes in v2.0).

### Convention

1. **`owner_id: UUID | None` is nullable.**
   - Set on creation by the router from `current_user.id` (or `None` in
     legacy/dev mode where `get_current_user` returns `None`).
   - **Never** mutated after creation. There is no "transfer ownership"
     endpoint in v1.7.
   - `None` means "single-user / legacy instance" — anyone can edit the
     row, the tier counter falls back to global.

2. **The repository exposes owner-scoped helpers, not generic ones.**
   - `count_for_owner(owner_id)` — the tier gate calls this, never
     `len(repo.list_all())`.
   - `list_for_owner(owner_id, *, include_trashed=False)` — the dashboard
     uses this; admins bypass the filter.
   - These helpers return zero rows for an unknown `owner_id`, never raise.

3. **Ownership check lives in the router, not the repository.**
   - `_check_owner(entity, user)` raises `403` if `user.id != entity.owner_id`,
     bypassed when `user.role == "admin"` or when `entity.owner_id is None`.
   - The repository remains agnostic about who is allowed to read which row;
     it is the router's job.

4. **Admin always sees everything.**
   - List endpoints check `role == "admin"` and call `list_all` instead of
     `list_for_owner` for that case.
   - This avoids the need for a separate `/admin/maps` endpoint.

5. **Soft-delete (`deleted_at`) does not count toward the tier limit.**
   - `count_for_owner` filters `WHERE deleted_at IS NULL`. A trashed map is
     not a "live" map.

6. **No multi-tenant isolation beyond ownership.**
   - There is no `org_id` column. Multi-org isolation is out of scope for
     v1.7; each instance is still single-org. If Cocarte v2.x introduces
     orgs, the `org_id` is added then, and `_check_owner` becomes
     `_check_access` with an org dimension.

### What this is NOT

- It is **not** RBAC. The role enum (`viewer | editor | admin | owner`)
  remains instance-wide; ownership is per-row and orthogonal.
- It is **not** a permission system. Ownership decides "can edit", not
  "can read" — public maps are readable by anyone regardless of `owner_id`.
- It is **not** retroactive. `Project`, `Trigger`, etc. keep their global
  tier counters until/unless their own UX requires per-user quotas.

## Consequences

### Positive

- Cocarte's "5 maps free per user" wedge is implementable without changing
  any existing entity.
- Future user-facing entities (mini-apps, comments, gallery items) have a
  template to follow.
- Admin operations remain simple: one role check at the router level.
- Single-user/legacy instances continue to work unchanged: `owner_id IS NULL`
  is a valid state.

### Negative

- Two patterns co-exist: global-counted (`Project`, `Trigger`, …) and
  owner-counted (`CocarteMap`). Reviewers must remember which is which.
- The `_check_owner` boilerplate duplicates across endpoints in
  `maps_router.py`. If we add a third or fourth user-facing entity,
  extract a `Depends(require_owner(repo))` dependency.
- Switching an existing global-counted entity to owner-counted is a
  schema migration — not free. Picking which entities are user-scoped
  is a deliberate product decision per release.

### Migration of existing instances

Adding `owner_id` to a brand-new table (`maps`) is non-breaking. There is
no migration of existing rows because there are none. If a future ADR
extends ownership to `Project` or `Trigger`, that ADR will define the
backfill (likely: `owner_id = NULL` for all pre-existing rows, treated as
single-user / shared).

## Alternatives considered

- **Reuse `Project.owner_id` and forbid Cocarte without a Project.** Rejected:
  the v1.7 onboarding wizard explicitly supports "drop a GeoJSON, publish
  immediately" without a Project.
- **Multi-tenant from day 1 (`org_id` everywhere).** Rejected: scope creep.
  Cocarte ships per-user quotas, not orgs.
- **Backfill `owner_id` on every existing entity.** Rejected: out of scope
  for v1.7 and risks regression on demo VPS.

## References

- Sprint plan: [/home/simon/.claude/plans/cocarte_sprint_plan_v17_v20_2026_05_09.md](../../../../../.claude/plans/cocarte_sprint_plan_v17_v20_2026_05_09.md)
- Map entity design: [/home/simon/.claude/plans/cocarte_map_entity_design_2026_05_10.md](../../../../../.claude/plans/cocarte_map_entity_design_2026_05_10.md)
- Backend PR: [#168](https://github.com/imagodata/gispulse/pull/168)
- Frontend PR: [imagodata/gispulse-portal#100](https://github.com/imagodata/gispulse-portal/pull/100)
