/**
 * relativeTime — small util shared by admin tabs.
 *
 * If you already have an equivalent in src/utils/, delete this file and
 * adjust the import paths in ProcessingView/ModelsView/AlertsView.
 */

export function relativeTime(iso: string | undefined | null): string {
  if (!iso) return '—';
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '—';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}
