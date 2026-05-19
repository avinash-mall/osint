/**
 * Shared admin-tab section header.
 * Extracted so ProcessingView / ModelsView / AlertsView don't duplicate it.
 */

import type { ReactNode } from 'react';

type Props = {
  title: string;
  sub: string;
  actions?: ReactNode;
};

export default function ViewHeader({ title, sub, actions }: Props) {
  return (
    <div
      className="admin-view-header"
      style={{
        padding: '16px 22px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        alignItems: 'flex-end',
        gap: 14,
        flexWrap: 'wrap',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 16, fontWeight: 600, lineHeight: 1.2 }}>{title}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
          {sub}
        </div>
      </div>
      <div style={{ flex: 1 }}/>
      <div className="admin-view-actions" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {actions}
      </div>
    </div>
  );
}
