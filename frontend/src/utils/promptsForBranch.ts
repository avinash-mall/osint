/**
 * Branch-scoped prompt collector.
 *
 * Walks an ontology branch (and, by default, its descendant sub-branches)
 * and returns the unique SAM 3 prompts the operator should send when
 * scoping an ingest to that mission branch. Sentinel markers
 * (`__prithvi_*__`) are filtered out — they exist for legend purposes and
 * are not real text prompts.
 *
 * Pure function — no React, no fetches. Lives outside the component so the
 * mode-state logic stays unit-testable even though the project has no
 * Vitest setup today.
 *
 * See `docs/decisions/why-branch-scoped-default.md` for the why.
 */
import { isSam3Prompt } from './defenceOntology';
import type { OntologyBranch } from './useOntology';

export function promptsForBranch(
  branch: OntologyBranch,
  includeDescendants = true,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const collect = (b: OntologyBranch) => {
    for (const obj of b.objects || []) {
      const p = obj.prompt;
      if (!p || !isSam3Prompt(p)) continue;
      const key = p.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(p);
    }
    if (includeDescendants) {
      for (const child of b.children || []) collect(child);
    }
  };
  collect(branch);
  return out;
}

/**
 * Flatten the entire tree to a single deduplicated SAM 3 prompt list. Used
 * for the "All branches" opt-out mode where the operator deliberately
 * wants full open-vocab fan-out.
 */
export function promptsForAllBranches(branches: OntologyBranch[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const root of branches) {
    for (const p of promptsForBranch(root, true)) {
      const key = p.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(p);
    }
  }
  return out;
}
