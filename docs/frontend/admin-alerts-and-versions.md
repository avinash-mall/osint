# Admin — Alerts + Taxonomy Version

**Paths:**
- [frontend/src/components/admin/AlertsView.tsx](../../frontend/src/components/admin/AlertsView.tsx) (~4065 chars)
- [frontend/src/components/admin/TaxonomyVersionView.tsx](../../frontend/src/components/admin/TaxonomyVersionView.tsx) (~4997 chars)

## AlertsView

Operator alerts derived from `/api/alerts`. Failed ingest tasks, degraded services, GPU OOM events. Updates in near-real-time via the `health_alert` WS topic.

- `GET /api/alerts`
- WS: `health_alert`

## TaxonomyVersionView

Ontology audit log: every edit (branch/object/prompt-profile) bumps a version and writes a row here.

- `GET /api/ontology/version-history`

## Cross-references

- [backend-routers/health-router.md](../backend-routers/health-router.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [operations/health-monitoring.md](../operations/health-monitoring.md)
