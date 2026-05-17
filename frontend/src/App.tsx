/**
 * Sentinel · GEOINT Workstation entry point.
 *
 * Mounts the redesigned Modern shell with four workspaces:
 *   - map   → GEOINT Common Operating Picture
 *   - fmv   → Drone Video player
 *   - graph → Link / Entity graph
 *   - admin → Ontology + Upload + Processing + Models + Alerts + Auth (LDAP)
 *
 * Wraps the app in AuthProvider so every API call gets the session cookie and
 * the login screen renders until /api/auth/me succeeds. Threads two pieces of
 * cross-workspace state through the children:
 *   - cursor lat/lng (set by GaiaMap & FmvPlayer, rendered in Shell's statusRight)
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
import { AuthProvider, useAuth } from './hooks/useAuth';

const CONTEXT_LINE: Record<WorkspaceKey, string> = {
  ingest: 'Ingest · upload imagery, video, and feeds',
  map:    'Common Operating Picture · live detections + imagery',
  fmv:    'Full-motion video · synced map · MISB 0601 telemetry',
  graph:  'Entity graph · Neo4j-backed link analysis',
  admin:  'Ontology · processing · models · alerts · auth',
};

export type CursorPosition = { lat: number; lon: number } | null;
export type CrossNavTarget = {
  /** Workspace to switch to. */
  workspace: WorkspaceKey;
  /** Detection / clip id the target should reveal. */
  detectionId?: number;
  fmvClipId?: number;
  className?: string;
};

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}

function Gate() {
  const { status } = useAuth();
  if (status === 'loading') {
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'grid',
          placeItems: 'center',
          background: 'var(--bg-0)',
          color: 'var(--ink-2)',
          fontFamily: 'var(--font-mono)',
          fontSize: 12,
        }}
      >
        AUTHENTICATING …
      </div>
    );
  }
  if (status !== 'authenticated') {
    return <LoginScreen />;
  }
  return <AuthedApp />;
}

function AuthedApp() {
  const [active, setActive] = useState<WorkspaceKey>('map');
  const [cursor, setCursor] = useState<CursorPosition>(null);
  const [crossNav, setCrossNav] = useState<CrossNavTarget | null>(null);

  const onNavigate = useCallback((key: WorkspaceKey) => {
    setActive(key);
    // Switching workspaces clears the cursor display (each workspace sets its own).
    setCursor(null);
  }, []);

  const requestCrossNav = useCallback((target: CrossNavTarget) => {
    setActive(target.workspace);
    setCrossNav(target);
  }, []);

  const consumeCrossNav = useCallback(() => {
    setCrossNav(null);
  }, []);

  return (
    <Shell
      active={active}
      onNavigate={onNavigate}
      contextLine={CONTEXT_LINE[active]}
      statusRight={cursor ? <CursorReadout cursor={cursor} /> : undefined}
    >
      {active === 'ingest' && <IngestConnect />}
      {active === 'map'   && (
        <GaiaMap
          onOpenGraph={() => onNavigate('graph')}
          onOpenFmv={(clipId) => requestCrossNav({ workspace: 'fmv', fmvClipId: clipId })}
          onCursorChange={setCursor}
          crossNav={crossNav?.workspace === 'map' ? crossNav : null}
          consumeCrossNav={consumeCrossNav}
        />
      )}
      {active === 'fmv'   && (
        <FmvPlayer
          onCursorChange={setCursor}
          onOpenMap={(detectionId) => requestCrossNav({ workspace: 'map', detectionId })}
          crossNav={crossNav?.workspace === 'fmv' ? crossNav : null}
          consumeCrossNav={consumeCrossNav}
        />
      )}
      {active === 'graph' && <GraphExplorer />}
      {active === 'admin' && (
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

function CursorReadout({ cursor }: { cursor: NonNullable<CursorPosition> }) {
  return (
    <span
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 10.5,
        color: 'var(--ink-1)',
      }}
      title="Cursor latitude / longitude (WGS84)"
    >
      <span style={{ color: 'var(--ink-2)' }}>LAT</span>
      <span style={{ color: 'var(--ink-0)', minInlineSize: '4rem', textAlign: 'right' }}>
        {cursor.lat.toFixed(4)}° {cursor.lat >= 0 ? 'N' : 'S'}
      </span>
      <span style={{ width: 1, height: 12, background: 'var(--line-2)' }} />
      <span style={{ color: 'var(--ink-2)' }}>LON</span>
      <span style={{ color: 'var(--ink-0)', minInlineSize: '4rem', textAlign: 'right' }}>
        {Math.abs(cursor.lon).toFixed(4)}° {cursor.lon >= 0 ? 'E' : 'W'}
      </span>
    </span>
  );
}
