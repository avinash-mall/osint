# Frontend Utils — Ontology + Icons

**Paths:**
- [frontend/src/utils/branchIcons.tsx](../../frontend/src/utils/branchIcons.tsx) — branch → icon component map
- [frontend/src/utils/defenceOntology.ts](../../frontend/src/utils/defenceOntology.ts) — `BranchId` constants and color/glyph defaults
- [frontend/src/utils/detectionTaxonomy.ts](../../frontend/src/utils/detectionTaxonomy.ts) — `CLASS_LIST` flattening, taxon → color lookup, memoized cache
- [frontend/src/utils/iconLibrary.tsx](../../frontend/src/utils/iconLibrary.tsx) — `lucide-react` re-exports plus custom threat/affiliation symbols
- [frontend/src/utils/objectMetadata.ts](../../frontend/src/utils/objectMetadata.ts) — accessors for a detection record's fields (threat scoring, provenance extraction)
- [frontend/src/utils/ontologyApi.ts](../../frontend/src/utils/ontologyApi.ts) — thin fetch wrapper over `/api/ontology/*`
- [frontend/src/utils/useOntology.ts](../../frontend/src/utils/useOntology.ts) — React hook: fetches the ontology tree, exposes `flattenBranches`, refresh on `ontology_updated` WS event
- [frontend/src/utils/uploadProgress.ts](../../frontend/src/utils/uploadProgress.ts) — `UploadJob` type and progress derivations

## Why these are shared

Every workspace touches ontology lookups (branch icon, taxon color, normalized label). Putting them in `utils/` means the workspaces stay focused on their UX, and the ontology mapping has a single live source — the React hook subscribes to ontology version changes so all consumers refresh together.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [ontology-admin-ui.md](ontology-admin-ui.md) — primary consumer
