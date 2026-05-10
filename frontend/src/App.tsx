import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Clock3,
  Crosshair,
  Map as MapIcon,
  Settings,
  UploadCloud,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import GaiaMap from './components/GaiaMap';
import GraphExplorer from './components/GraphExplorer';
import IngestConnect from './components/IngestConnect';
import OntologyAdmin from './components/OntologyAdmin';

const API_URL = import.meta.env.VITE_API_URL || '';

type WorkspaceKey = 'map' | 'graph' | 'ingest' | 'admin';

type HealthStatus = {
  healthy: boolean;
  neo4j?: string;
  postgis?: string;
};

const workspaces: Array<{
  key: WorkspaceKey;
  title: string;
  short: string;
  tooltip: string;
  icon: LucideIcon;
}> = [
  { key: 'map', title: 'GEOINT / Map Common Operating Picture', short: 'GEO', tooltip: 'GEOINT Map', icon: MapIcon },
  { key: 'graph', title: 'Link Analysis / Entity Graph', short: 'LINK', tooltip: 'Ontology Explorer', icon: Crosshair },
  { key: 'ingest', title: 'Ingest / Feeds & Pipelines', short: 'FEEDS', tooltip: 'Ingest & Streams', icon: UploadCloud },
  { key: 'admin', title: 'Ontology Admin', short: 'ADMIN', tooltip: 'Ontology Admin', icon: Settings },
];

function useClock() {
  const [clock, setClock] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return clock;
}

function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceKey>('map');
  const [health, setHealth] = useState<HealthStatus>({
    healthy: false,
    neo4j: 'unknown',
    postgis: 'unknown',
  });
  const [uploadCount, setUploadCount] = useState(0);
  const clock = useClock();

  const activeWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.key === activeTab) || workspaces[0],
    [activeTab],
  );

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const response = await axios.get<HealthStatus>(`${API_URL}/api/health`);
        setHealth(response.data);
      } catch {
        setHealth({ healthy: false, neo4j: 'error', postgis: 'error' });
      }
    };

    const fetchUploads = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/ingest/uploads`);
        setUploadCount(response.data.uploads?.length || 0);
      } catch {
        setUploadCount(0);
      }
    };

    fetchHealth();
    fetchUploads();
    const id = window.setInterval(() => {
      fetchHealth();
      fetchUploads();
    }, 15000);
    return () => window.clearInterval(id);
  }, []);

  const renderContent = () => {
    switch (activeTab) {
      case 'graph':
        return <GraphExplorer />;
      case 'ingest':
        return <IngestConnect />;
      case 'admin':
        return <OntologyAdmin />;
      case 'map':
      default:
        return <GaiaMap onOpenGraph={() => setActiveTab('graph')} />;
    }
  };

  return (
    <div className="sentinel-app">
      <div className="workspace">
        <aside className="sentinel-sidebar">
          <div className="sentinel-brand" title="Sentinel Workstation">SNT</div>
          <nav className="sentinel-nav" aria-label="Primary workspace navigation">
            {workspaces.map((workspace) => {
              const Icon = workspace.icon;
              const selected = activeTab === workspace.key;
              return (
                <button
                  key={workspace.key}
                  onClick={() => setActiveTab(workspace.key)}
                  className={`sentinel-nav-item ${selected ? 'on' : ''}`}
                  title={workspace.tooltip}
                  type="button"
                >
                  <Icon size={16} />
                  <span>{workspace.short}</span>
                </button>
              );
            })}
          </nav>
        </aside>

        <div className="sentinel-main">
          <header className="sentinel-topbar">
            <div className="min-w-0">
              <div className="sentinel-crumbs">
                <span>SENTINEL</span>
                <span>/</span>
                <span className="text-sentinel-text">{activeWorkspace.title}</span>
              </div>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="sentinel-tag info">LIVE</span>
              <span className={`sentinel-tag ${health.healthy ? 'ok' : 'crit'}`}>API {health.healthy ? 'ONLINE' : 'DEGRADED'}</span>
              <span className={`sentinel-tag ${health.neo4j === 'ok' ? 'ok' : 'crit'}`}>ONTOLOGY {health.neo4j === 'ok' ? 'SYNCED' : 'OFFLINE'}</span>
              <span className={`sentinel-tag ${health.postgis === 'ok' ? 'ok' : 'crit'}`}>POSTGIS {health.postgis === 'ok' ? 'READY' : 'OFFLINE'}</span>
              <div className="font-mono text-[11px] text-sentinel-muted">
                <span className="text-sentinel-text">ZULU</span> {clock.toISOString().slice(11, 19)}
              </div>
            </div>
          </header>

          <main className="sentinel-view">
            {renderContent()}
          </main>

          <footer className="sentinel-statusbar">
            <div className="status-group"><span className={`sentinel-dot ${health.healthy ? 'ok' : 'crit'}`} /> OPLINK <span>NOMINAL</span></div>
            <div className="status-group">UPLOADS <span>{uploadCount}</span></div>
            <div className="ml-auto status-group">VIEW <span>{activeTab}</span></div>
            <div className="status-group"><Clock3 size={12} /> {clock.toISOString().slice(0, 10)}</div>
          </footer>
        </div>
      </div>
    </div>
  );
}

export default App;
