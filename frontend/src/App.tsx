import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Activity,
  Bot,
  Box,
  Clock3,
  Crosshair,
  Database,
  Globe,
  Map as MapIcon,
  MessageSquare,
  RadioTower,
  Search,
  Target,
  UploadCloud,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import AvaChat from './components/AvaChat';
import Browser from './components/Browser';
import ConstellationView from './components/ConstellationView';
import GaiaMap from './components/GaiaMap';
import GraphExplorer from './components/GraphExplorer';
import IngestConnect from './components/IngestConnect';
import SentinelDashboard from './components/SentinelDashboard';
import SentinelWatch from './components/SentinelWatch';
import TargetWorkbench from './components/TargetWorkbench';
import View3D from './components/View3D';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

type WorkspaceKey =
  | 'dashboard'
  | 'map'
  | 'ops'
  | 'targets'
  | 'space'
  | 'graph'
  | 'browser'
  | 'ingest'
  | 'view3d'
  | 'ai';

type HealthStatus = {
  healthy: boolean;
  neo4j?: string;
  postgis?: string;
  ai?: {
    configured?: boolean;
    model?: string;
  };
};

type Classification = {
  top_banner?: string | null;
  bottom_banner?: string | null;
  caveat?: string;
  generated_at?: string;
  model?: string;
  status: 'ok' | 'unavailable' | string;
};

const workspaces: Array<{
  key: WorkspaceKey;
  title: string;
  short: string;
  tooltip: string;
  icon: LucideIcon;
}> = [
  { key: 'dashboard', title: 'Operations / Fusion Overview', short: 'OPS', tooltip: 'Operations Overview', icon: Activity },
  { key: 'map', title: 'GEOINT / Map Common Operating Picture', short: 'GEO', tooltip: 'GEOINT Map', icon: MapIcon },
  { key: 'ops', title: 'Watch Floor / Live Operations', short: 'WATCH', tooltip: 'Watch Floor', icon: RadioTower },
  { key: 'targets', title: 'HPTL / Target Workbench', short: 'HPTL', tooltip: 'Target Workbench', icon: Target },
  { key: 'space', title: 'Constellation / Tasking & Coverage', short: 'CONST', tooltip: 'Collection Planning', icon: Globe },
  { key: 'graph', title: 'Link Analysis / Entity Graph', short: 'LINK', tooltip: 'Ontology Explorer', icon: Crosshair },
  { key: 'browser', title: 'Data Browser / Records', short: 'TABLE', tooltip: 'Data Browser', icon: Database },
  { key: 'ingest', title: 'Ingest / Feeds & Pipelines', short: 'FEEDS', tooltip: 'Ingest & Streams', icon: UploadCloud },
  { key: 'view3d', title: 'Globe / 3D View', short: '3D', tooltip: '3D Globe', icon: Box },
  { key: 'ai', title: 'AVA / AI Analyst', short: 'AVA', tooltip: 'AI Analyst', icon: MessageSquare },
];

function useClock() {
  const [clock, setClock] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return clock;
}

function ClassificationBanner({ side, classification }: { side: 'top' | 'bottom'; classification: Classification }) {
  const unavailable = classification.status !== 'ok';
  const text = side === 'top' ? classification.top_banner : classification.bottom_banner;
  return (
    <div className={`classification ${side === 'bottom' ? 'bottom' : ''}`}>
      <div className="marks">
        <span>{unavailable ? 'LLM CLASSIFICATION UNAVAILABLE' : text}</span>
        <span className="opacity-50">/</span>
        <span>{classification.model || 'MODEL UNAVAILABLE'}</span>
        <span className="opacity-50">/</span>
        <span>{unavailable ? classification.caveat || 'NO GENERATED BANNER TEXT' : classification.caveat || 'GENERATED FROM CURRENT WORKSPACE CONTEXT'}</span>
      </div>
    </div>
  );
}

function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceKey>('map');
  const [health, setHealth] = useState<HealthStatus>({
    healthy: false,
    neo4j: 'unknown',
    postgis: 'unknown',
    ai: { configured: false },
  });
  const [uploadCount, setUploadCount] = useState(0);
  const [classification, setClassification] = useState<Classification>({ status: 'unavailable', caveat: 'Awaiting LLM classification.' });
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
        setHealth({ healthy: false, neo4j: 'error', postgis: 'error', ai: { configured: false } });
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

  useEffect(() => {
    const fetchClassification = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/ui/classification`, {
          params: { workspace: activeTab },
          timeout: 8000,
        });
        setClassification(response.data);
      } catch {
        setClassification({ status: 'unavailable', caveat: 'Unable to reach LLM classification endpoint.' });
      }
    };
    fetchClassification();
    const id = window.setInterval(fetchClassification, 120000);
    return () => window.clearInterval(id);
  }, [activeTab]);

  const renderContent = () => {
    switch (activeTab) {
      case 'dashboard':
        return <SentinelDashboard />;
      case 'ops':
        return <SentinelWatch />;
      case 'graph':
        return <GraphExplorer />;
      case 'map':
        return <GaiaMap onOpenWorkbench={() => setActiveTab('targets')} onOpenGraph={() => setActiveTab('graph')} />;
      case 'targets':
        return <TargetWorkbench />;
      case 'space':
        return <ConstellationView />;
      case 'browser':
        return <Browser />;
      case 'ingest':
        return <IngestConnect />;
      case 'view3d':
        return <View3D />;
      case 'ai':
        return <AvaChat />;
      default:
        return <GaiaMap onOpenWorkbench={() => setActiveTab('targets')} onOpenGraph={() => setActiveTab('graph')} />;
    }
  };

  return (
    <div className="sentinel-app">
      <ClassificationBanner side="top" classification={classification} />
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
          <div className="mt-auto flex flex-col gap-2">
            <button className="sentinel-icon-btn" type="button" title="Search">
              <Search size={15} />
            </button>
            <button className="sentinel-icon-btn" type="button" title="AI">
              <Bot size={15} />
            </button>
          </div>
        </aside>

        <div className="sentinel-main">
          <header className="sentinel-topbar">
            <div className="min-w-0">
              <div className="sentinel-crumbs">
                <span>SENTINEL</span>
                <span>/</span>
                <span className="text-sentinel-text">{activeWorkspace.title}</span>
              </div>
              <div className="truncate font-mono text-[10px] text-sentinel-muted">{classification.status === 'ok' ? classification.generated_at : classification.caveat}</div>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="sentinel-tag info">LIVE</span>
              <span className={`sentinel-tag ${health.healthy ? 'ok' : 'crit'}`}>API {health.healthy ? 'ONLINE' : 'DEGRADED'}</span>
              <span className={`sentinel-tag ${health.neo4j === 'ok' ? 'ok' : 'crit'}`}>ONTOLOGY {health.neo4j === 'ok' ? 'SYNCED' : 'OFFLINE'}</span>
              <span className={`sentinel-tag ${health.postgis === 'ok' ? 'ok' : 'crit'}`}>POSTGIS {health.postgis === 'ok' ? 'READY' : 'OFFLINE'}</span>
              <span className={`sentinel-tag ${health.ai?.configured ? 'ok' : 'warn'}`}>LLM {health.ai?.configured ? 'READY' : 'UNAVAILABLE'}</span>
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
            <div className="status-group">MODEL <span>{health.ai?.model || classification.model || 'n/a'}</span></div>
            <div className="status-group">CLASSIFICATION <span>{classification.status}</span></div>
            <div className="ml-auto status-group">VIEW <span>{activeTab}</span></div>
            <div className="status-group"><Clock3 size={12} /> {clock.toISOString().slice(0, 10)}</div>
          </footer>
        </div>
      </div>
      <ClassificationBanner side="bottom" classification={classification} />
    </div>
  );
}

export default App;
