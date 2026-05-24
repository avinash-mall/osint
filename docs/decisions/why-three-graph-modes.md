# Why the Link Graph has Three Modes

## Decision

The Link Graph workspace renders one of three modes at a time —
**Investigation**, **Evidence**, **Ontology** — controlled by a sub-tab strip
inside the existing `graph` workspace. Selection state (current node, time
range, AOI scope, class lens) is shared across modes.

## Why three (and not one)

The redesigned graph has to answer six concrete analyst workflows
([architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)).
Those workflows naturally split into three lenses on the same underlying
graph:

| Workflow | Lens |
|---|---|
| 1 ("why does this detection matter?"), 2 ("seen before?"), 3 ("what's at this site?"), 6 (transitive Cypher) | **Investigation** — instance graph |
| 5 (chain of evidence: Target → Detection → SatellitePass/FMVClip/Document → source/model/confidence) | **Evidence** — provenance DAG |
| 4 (ontology vs intelligence problem, unknown-label triage) | **Ontology** — class graph + UnknownLabel orbit |

A single mode would either be too dense (everything visible at once becomes
the "visually impressive but analytically weak" failure mode we're explicitly
avoiding) or too thin (one workflow served well, the others abandoned).

## Why sub-tabs inside ONE workspace

The alternative — three separate top-level workspaces in the icon rail
([Shell.tsx](../../frontend/src/components/Shell.tsx)) — fragments the
analyst's mental context:

- The current node, the active time window, the AOI scope, and the class
  lens are shared state across modes. Sibling workspaces would force the
  analyst to re-pick the node on every tab switch.
- "Open in Evidence mode" from an Investigation right-click is a single-tab
  transition that preserves the selection. Across workspaces it'd require
  passing IDs through global state or URL params.
- The icon-rail entry stays as `Share2` (`Link Graph`) per the UX-AUDIT F23
  pattern — no rail churn.

Sub-tabs inside one workspace keep the workspace as the unit of analyst
intent ("I want to work on the link graph"), with the mode as the unit of
*how* they want to work on it.

## What each mode sees (visibility rule)

- **Investigation:** Operational nodes (Target, Asset, Base, LaunchPoint,
  Facility, Unit, Vessel, Aircraft, Vehicle) always; Evidence nodes
  (Detection, FMVDetection, Observation, SatellitePass, FMVClip, Document,
  Report, FeedEvent) only inside the 2-hop neighborhood of the current
  selection; Ontology nodes hidden unless the class-lens chip is toggled or
  an Ontology node is on a returned path.
- **Evidence:** every node 2-hop from the focus, columnised by kind.
  Operational on the left, evidence types in columns to the right.
- **Ontology:** branches/objects/prompts + UnknownLabel orbit + co-occurrence
  chips; operational entities hidden.

This makes the "ontology nodes only appear when they explain evidence,
classification, unknown labels, or target relationships" rule
([architecture/link-graph-redesign.md#L83-L88](../architecture/link-graph-redesign.md#L83-L88))
enforceable per mode, not an unwritten convention.

## Trade-offs accepted

- **Two modes are stubs in Phase 1.** Ontology and Evidence tabs render a
  placeholder until Phase 3/Phase 2 land. The tab strip ships in Phase 1.E
  so the IA is visible early.
- **Selection state is mode-local where it has to be.** Time-range filter
  applies in Investigation only — Evidence is implicitly "everything
  related to the focus regardless of time," and Ontology is implicitly
  current-version.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [decisions/ux-audit-001.md](ux-audit-001.md) — the F22/F23 audit that
  established the predicate chip bar and Link Graph icon.
