# Admin Workspace — `AdminScreen.tsx`

**Path:** [frontend/src/components/AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx)

## Purpose

Ten-tab consolidation of all operator tooling.

## Tabs

| Tab | Component | Doc |
|---|---|---|
| Ontology | [OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx) | [ontology-admin-ui.md](ontology-admin-ui.md) |
| Upload imagery | [IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx) (shared with Ingest workspace) | [workspace-ingest.md](workspace-ingest.md) |
| Processing | [admin/ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx) | [admin-models-and-processing.md](admin-models-and-processing.md) |
| AI models | [admin/ModelsView.tsx](../../frontend/src/components/admin/ModelsView.tsx) | [admin-models-and-processing.md](admin-models-and-processing.md) |
| Health dashboard | [admin/HealthDashboardView.tsx](../../frontend/src/components/admin/HealthDashboardView.tsx) | [admin-health-dashboard.md](admin-health-dashboard.md) |
| Conf overrides | [admin/ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx) | [admin-conf-overrides.md](admin-conf-overrides.md) |
| Prompt profiles | [admin/PromptProfilesView.tsx](../../frontend/src/components/admin/PromptProfilesView.tsx) | [admin-prompt-profiles.md](admin-prompt-profiles.md) |
| Version history | [admin/TaxonomyVersionView.tsx](../../frontend/src/components/admin/TaxonomyVersionView.tsx) | [admin-alerts-and-versions.md](admin-alerts-and-versions.md) |
| Health alerts | [admin/AlertsView.tsx](../../frontend/src/components/admin/AlertsView.tsx) | [admin-alerts-and-versions.md](admin-alerts-and-versions.md) |
| Auth · LDAP | [AdminAuthTab.tsx](../../frontend/src/components/AdminAuthTab.tsx) | [admin-auth-ldap.md](admin-auth-ldap.md) |

The `admin/*View.tsx` files share a common pattern: small components, each calling a small set of REST endpoints. Shared header at [admin/ViewHeader.tsx](../../frontend/src/components/admin/ViewHeader.tsx). Timestamp formatter at [admin/time.ts](../../frontend/src/components/admin/time.ts).

## Cross-references

- [conventions/adding-a-new-admin-tab.md](../conventions/adding-a-new-admin-tab.md)
- [admin-health-dashboard.md](admin-health-dashboard.md), [admin-conf-overrides.md](admin-conf-overrides.md), etc.
