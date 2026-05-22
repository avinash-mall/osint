/**
 * AdminScreen — consolidates the four admin views into a single workspace:
 *   - Ontology  : delegates to the existing OntologyAdmin component (full CRUD)
 *   - Processing: live list of analytics + training jobs (POST/queued/running/done)
 *   - Models    : registered detection models, with one-click promotion
 *   - Alerts    : operator alert feed derived from /api/health + failed ingest tasks
 *
 * Every panel pulls from the real backend.  No mocked data.
 */

import { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Box,
  Cpu,
  Filter,
  GitBranch,
  HeartPulse,
  History,
  Key,
  Search,
} from 'lucide-react';
import OntologyAdmin from './OntologyAdmin';
import AdminAuthTab from './AdminAuthTab';
import HealthDashboardView from './admin/HealthDashboardView';
import ConfOverrideView from './admin/ConfOverrideView';
import PromptProfilesView from './admin/PromptProfilesView';
import TaxonomyVersionView from './admin/TaxonomyVersionView';
import ProcessingView from './admin/ProcessingView';
import ModelsView from './admin/ModelsView';
import ModelLoadingView from './admin/ModelLoadingView';
import AlertsView from './admin/AlertsView';

type AdminTab =
  | 'ontology'
  | 'processing'
  | 'models'
  | 'modelload'
  | 'alerts'
  | 'auth'
  | 'health'
  | 'confidence'
  | 'prompts'
  | 'versions';

type Counts = {
  processing: number;
  models: number;
  alerts: number;
};

type NavItemDef = {
  key: AdminTab;
  label: string;
  Icon: typeof Activity;
  badgeKey?: keyof Counts;
};

const NAV: NavItemDef[] = [
  { key: 'ontology',   label: 'Ontology',         Icon: GitBranch },
  { key: 'processing', label: 'Processing',       Icon: Activity, badgeKey: 'processing' },
  { key: 'models',     label: 'AI models',        Icon: Box,      badgeKey: 'models' },
  { key: 'modelload',  label: 'Model loading',    Icon: Cpu },
  { key: 'health',     label: 'Health dashboard', Icon: HeartPulse },
  { key: 'confidence', label: 'Conf overrides',   Icon: Filter },
  { key: 'prompts',    label: 'Prompt profiles',  Icon: Search },
  { key: 'versions',   label: 'Version history',  Icon: History },
  { key: 'alerts',     label: 'Health alerts',    Icon: AlertTriangle, badgeKey: 'alerts' },
  // UX-AUDIT F29: 'Auth · LDAP' surfaced an implementation detail as the
  // tab name. 'Sign-in & users' names the operator-facing function.
  { key: 'auth',       label: 'Sign-in & users',  Icon: Key },
];

type AdminScreenProps = {
  /** Switch to the GEOINT workspace focused on a specific detection. */
  onOpenDetectionOnMap?: (detectionId: number, className?: string) => void;
  /** Switch to the FMV workspace focused on a specific detection. */
  onOpenDetectionInFmv?: (detectionId: number) => void;
};

export default function AdminScreen({
  onOpenDetectionOnMap,
  onOpenDetectionInFmv,
}: AdminScreenProps = {}) {
  const [tab, setTab] = useState<AdminTab>('ontology');
  const [counts, setCounts] = useState<Counts>({ processing: 0, models: 0, alerts: 0 });

  // Listen for Shell's "jump to admin tab" events (e.g. Bell icon ⇒ alerts).
  useEffect(() => {
    const handler = (evt: Event) => {
      const detail = (evt as CustomEvent).detail || {};
      const target = String(detail.tab || '').toLowerCase();
      if (NAV.some((n) => n.key === target)) setTab(target as AdminTab);
    };
    window.addEventListener('sentinel:admin-tab', handler);
    return () => window.removeEventListener('sentinel:admin-tab', handler);
  }, []);

  return (
    <div
      className="admin-shell"
      style={{
        height: '100%',
        display: 'grid',
        gap: 1,
        background: 'var(--line)',
      }}
    >
      <nav
        className="panel admin-nav"
        style={{ border: 0, display: 'flex', flexDirection: 'column' }}
      >
        <div className="panel-h">
          <Cpu size={14} />
          <span className="h-title">Operations</span>
        </div>
        {NAV.map((n) => {
          const { Icon } = n;
          const active = tab === n.key;
          const badge = n.badgeKey ? counts[n.badgeKey] : undefined;
          return (
            <button
              key={n.key}
              type="button"
              onClick={() => setTab(n.key)}
              style={{
                display: 'grid',
                gridTemplateColumns: '24px 1fr auto',
                gap: 8,
                alignItems: 'center',
                padding: '10px 14px',
                border: 0,
                background: active ? 'var(--bg-2)' : 'transparent',
                borderLeft: active ? '2px solid var(--accent)' : '2px solid transparent',
                color: active ? 'var(--ink-0)' : 'var(--ink-1)',
                cursor: 'pointer',
                textAlign: 'left',
                fontSize: 12.5,
              }}
            >
              <Icon size={14} />
              <span>{n.label}</span>
              {badge != null && (
                <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                  {badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <section
        className="admin-content"
        style={{
          background: 'var(--bg-0)',
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        {tab === 'ontology'   && (
          <OntologyAdmin
            onOpenDetectionOnMap={onOpenDetectionOnMap}
            onOpenDetectionInFmv={onOpenDetectionInFmv}
          />
        )}
        {tab === 'processing' && (
          <ProcessingView
            onCount={(n) => setCounts((c) => ({ ...c, processing: n }))}
            onOpenOnMap={onOpenDetectionOnMap}
            onOpenInFmv={onOpenDetectionInFmv}
          />
        )}
        {tab === 'models'     && <ModelsView onCount={(n) => setCounts((c) => ({ ...c, models: n }))} />}
        {tab === 'modelload'  && <ModelLoadingView />}
        {tab === 'alerts'     && <AlertsView onCount={(n) => setCounts((c) => ({ ...c, alerts: n }))} />}
        {tab === 'auth'       && <AdminAuthTab />}
        {tab === 'health'     && <HealthDashboardView />}
        {tab === 'confidence' && <ConfOverrideView />}
        {tab === 'prompts'    && <PromptProfilesView />}
        {tab === 'versions'   && <TaxonomyVersionView />}
      </section>
    </div>
  );
}
