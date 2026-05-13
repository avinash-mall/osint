/**
 * Sentinel · GEOINT Workstation entry point.
 *
 * Mounts the redesigned Modern shell with four workspaces:
 *   - map   → GEOINT Common Operating Picture (real Leaflet w/ streets, detections)
 *   - fmv   → Drone Video player (real HLS, KLV telemetry, synced map)
 *   - graph → Link / Entity graph (Neo4j-backed force graph)
 *   - admin → Ontology + Upload + Processing + Models + Alerts
 *
 * The old `ingest` workspace has been folded into Admin · Upload (matching the
 * design intent — uploads also surface contextually in the map's Imagery tab and
 * the FMV player's Upload tab).
 */

import { useState } from 'react';
import AdminScreen from './components/AdminScreen';
import FmvPlayer from './components/FmvPlayer';
import GaiaMap from './components/GaiaMap';
import GraphExplorer from './components/GraphExplorer';
import { Shell } from './components/Shell';
import type { WorkspaceKey } from './components/Shell';

const CONTEXT_LINE: Record<WorkspaceKey, string> = {
  map:   'Common Operating Picture · live detections + imagery',
  fmv:   'Full-motion video · synced map · MISB 0601 telemetry',
  graph: 'Entity graph · Neo4j-backed link analysis',
  admin: 'Ontology · uploads · processing · models · alerts',
};

export default function App() {
  const [active, setActive] = useState<WorkspaceKey>('map');

  return (
    <Shell active={active} onNavigate={setActive} contextLine={CONTEXT_LINE[active]}>
      {active === 'map'   && <GaiaMap onOpenGraph={() => setActive('graph')} />}
      {active === 'fmv'   && <FmvPlayer />}
      {active === 'graph' && <GraphExplorer />}
      {active === 'admin' && <AdminScreen />}
    </Shell>
  );
}
