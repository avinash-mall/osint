/**
 * Sentinel · GEOINT Workstation entry point.
 *
 * Mounts the redesigned Modern shell with five workspaces:
 *   - ingest → upload imagery, video, and feeds
 *   - map    → GEOINT Common Operating Picture
 *   - fmv    → Drone Video player
 *   - graph  → Link / Entity graph
 *   - admin  → Ontology + Processing + Models + Alerts + Auth (LDAP)
 *
 * Wraps the app in AuthProvider so every API call gets the session cookie and
 * the login screen renders until /api/auth/me succeeds. Threads two pieces of
 * cross-workspace state through the children:
 *   - cursor lat/lng (set by GaiaMap & FmvPlayer; persists across switches so the
 *     statusbar layout doesn't reflow when the operator changes workspace)
 *   - selected detection for cross-nav (Ontology → GEOINT/FMV)
 */

import { useCallback, useState } from 'react';
import AdminScreen from './components/AdminScreen';
import FmvPlayer from './components/FmvPlayer';
import GaiaMap from './components/GaiaMap';
import GraphExplorer from './components/GraphExplorer';
import IngestConnect from './components/IngestConnect';
import LoginScreen from './components/LoginScreen';
import { Shell } from './components/Shell';
import type { WorkspaceKey } from './components/Shell';
import { CursorReadout, type CursorPos } from './components/atoms';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { PreferencesProvider } from './hooks/usePreferences';

export type CrossNavTarget = {
  workspace: WorkspaceKey;
  detectionId?: number;
  fmvClipId?: number;
  className?: string;
};

export default function App() {
  return (
    <PreferencesProvider>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </PreferencesProvider>
  );
}

function Gate() {
  const { status } = useAuth();
  if (status === 'loading') {
    return (
      <div role="status" aria-live="polite" style={{
        width: '100%', height: '100%',
        display: 'grid', placeItems: 'center',
        background: 'var(--bg-0)', color: 'var(--ink-2)',
        fontFamily: 'var(--font-mono)', fontSize: 12,
      }}>
        AUTHENTICATING …
      </div>
    );
  }
  if (status !== 'authenticated') return <LoginScreen />;
  return <AuthedApp />;
}

function AuthedApp() {
  const { user } = useAuth();
  const [active, setActive] = useState<WorkspaceKey>('map');
  const [cursor, setCursor] = useState<CursorPos>(null);
  const [crossNav, setCrossNav] = useState<CrossNavTarget | null>(null);
  const canUseAdmin = user?.role === 'admin';

  /**
   * Switching workspaces no longer clears the cursor — the readout stays
   * stable until the new workspace reports its own coordinates, so the
   * statusbar doesn't reflow on every tab change.
   */
  const onNavigate = useCallback((key: WorkspaceKey) => {
    if (key === 'admin' && !canUseAdmin) return;
    setActive(key);
  }, [canUseAdmin]);

  const requestCrossNav = useCallback((target: CrossNavTarget) => {
    if (target.workspace === 'admin' && !canUseAdmin) return;
    setActive(target.workspace);
    setCrossNav(target);
  }, [canUseAdmin]);

  const consumeCrossNav = useCallback(() => {
    setCrossNav(null);
  }, []);

  return (
    <Shell
      active={active}
      onNavigate={onNavigate}
      canUseAdmin={canUseAdmin}
      statusRight={<CursorReadout cursor={cursor} />}
    >
      {active === 'ingest' && <IngestConnect />}
      {active === 'map' && (
        <GaiaMap
          onOpenGraph={() => onNavigate('graph')}
          onOpenFmv={(clipId) => requestCrossNav({ workspace: 'fmv', fmvClipId: clipId })}
          onCursorChange={setCursor}
          crossNav={crossNav?.workspace === 'map' ? crossNav : null}
          consumeCrossNav={consumeCrossNav}
        />
      )}
      {active === 'fmv' && (
        <FmvPlayer
          onCursorChange={setCursor}
          onOpenMap={(detectionId) => requestCrossNav({ workspace: 'map', detectionId })}
          crossNav={crossNav?.workspace === 'fmv' ? crossNav : null}
          consumeCrossNav={consumeCrossNav}
        />
      )}
      {active === 'graph' && <GraphExplorer />}
      {active === 'admin' && canUseAdmin && (
        <AdminScreen
          onOpenDetectionOnMap={(detectionId, className) =>
            requestCrossNav({ workspace: 'map', detectionId, className })
          }
          onOpenDetectionInFmv={(detectionId) =>
            requestCrossNav({ workspace: 'fmv', detectionId })
          }
        />
      )}
    </Shell>
  );
}
