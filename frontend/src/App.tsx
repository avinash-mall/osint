import { useEffect, useState } from 'react';
import axios from 'axios';
import {
  Activity,
  Box,
  Database,
  Globe,
  Hexagon,
  Map as MapIcon,
  MessageSquare,
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
import TargetWorkbench from './components/TargetWorkbench';
import View3D from './components/View3D';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

type WorkspaceKey =
  | 'graph'
  | 'map'
  | 'targets'
  | 'space'
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
  };
};

const workspaces: Array<{
  key: WorkspaceKey;
  title: string;
  tooltip: string;
  icon: LucideIcon;
  accent: string;
  active: string;
}> = [
  {
    key: 'map',
    title: 'GEOINT :: Map Workspace',
    tooltip: 'GEOINT Map',
    icon: MapIcon,
    accent: '#38bdf8',
    active: 'bg-sky-500/20 text-sky-300',
  },
  {
    key: 'targets',
    title: 'Targets :: Workbench',
    tooltip: 'Target Workbench',
    icon: Target,
    accent: '#f87171',
    active: 'bg-red-500/20 text-red-300',
  },
  {
    key: 'space',
    title: 'Collection :: Space & Passes',
    tooltip: 'Collection Planning',
    icon: Globe,
    accent: '#818cf8',
    active: 'bg-indigo-500/20 text-indigo-300',
  },
  {
    key: 'ingest',
    title: 'Ingest :: Collections & Streams',
    tooltip: 'Ingest & Streams',
    icon: UploadCloud,
    accent: '#34d399',
    active: 'bg-emerald-500/20 text-emerald-300',
  },
  {
    key: 'graph',
    title: 'Ontology :: Entity Graph',
    tooltip: 'Ontology Explorer',
    icon: Activity,
    accent: '#60a5fa',
    active: 'bg-blue-500/20 text-blue-300',
  },
  {
    key: 'browser',
    title: 'Data :: Browser',
    tooltip: 'Data Browser',
    icon: Database,
    accent: '#60a5fa',
    active: 'bg-blue-500/20 text-blue-300',
  },
  {
    key: 'view3d',
    title: 'Globe :: 3D View',
    tooltip: '3D Globe',
    icon: Box,
    accent: '#22d3ee',
    active: 'bg-cyan-500/20 text-cyan-300',
  },
  {
    key: 'ai',
    title: 'AI Analyst :: Grounded Assistant',
    tooltip: 'AI Analyst',
    icon: MessageSquare,
    accent: '#10b981',
    active: 'bg-emerald-500/20 text-emerald-300',
  },
];

function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceKey>('map');
  const [health, setHealth] = useState<HealthStatus>({
    healthy: false,
    neo4j: 'unknown',
    postgis: 'unknown',
    ai: { configured: false },
  });

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const response = await axios.get<HealthStatus>(`${API_URL}/api/health`);
        setHealth(response.data);
      } catch {
        setHealth({ healthy: false, neo4j: 'error', postgis: 'error', ai: { configured: false } });
      }
    };

    fetchHealth();
    const id = window.setInterval(fetchHealth, 15000);
    return () => window.clearInterval(id);
  }, []);

  const renderContent = () => {
    switch (activeTab) {
      case 'graph':
        return <GraphExplorer />;
      case 'map':
        return <GaiaMap />;
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
        return <GaiaMap />;
    }
  };

  const activeWorkspace = workspaces.find((workspace) => workspace.key === activeTab) || workspaces[0];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 text-gray-200 font-sans">
      <aside className="w-16 bg-slate-900 border-r border-slate-700 flex flex-col items-center py-4 space-y-8 z-20 shadow-[4px_0_24px_rgba(0,0,0,0.5)]">
        <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center mb-2 shadow-lg shadow-blue-500/30" title="SentinelOS">
          <Hexagon className="text-white" size={24} fill="currentColor" />
        </div>

        <nav className="flex flex-col space-y-4 w-full px-2" aria-label="Primary workspace navigation">
          {workspaces.map((workspace) => {
            const Icon = workspace.icon;
            const selected = activeTab === workspace.key;
            return (
              <button
                key={workspace.key}
                onClick={() => setActiveTab(workspace.key)}
                className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${
                  selected ? workspace.active : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                }`}
                style={selected ? { boxShadow: `inset 2px 0 0 0 ${workspace.accent}` } : undefined}
                title={workspace.tooltip}
                type="button"
              >
                <Icon size={22} />
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 relative flex flex-col min-h-0 min-w-0">
        <header className="h-14 bg-slate-900/95 backdrop-blur border-b border-slate-700 flex items-center px-6 justify-between shadow-md z-10">
          <div className="flex items-center space-x-3 min-w-0">
            <h1 className="text-sm font-bold text-slate-100 tracking-widest uppercase truncate">
              {activeWorkspace.title}
            </h1>
          </div>
          <div className="flex items-center space-x-6 text-xs text-slate-400 font-mono">
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.healthy ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'}`} />
              <span>
                API <span className={health.healthy ? 'text-emerald-400' : 'text-red-400'}>{health.healthy ? 'ONLINE' : 'DEGRADED'}</span>
              </span>
            </div>
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.neo4j === 'ok' ? 'bg-emerald-500' : 'bg-red-500'}`} />
              <span>
                ONTOLOGY <span className={health.neo4j === 'ok' ? 'text-emerald-400' : 'text-red-400'}>{health.neo4j === 'ok' ? 'SYNCED' : 'OFFLINE'}</span>
              </span>
            </div>
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.ai?.configured ? 'bg-emerald-500' : 'bg-amber-500'}`} />
              <span>
                LLM <span className={health.ai?.configured ? 'text-emerald-400' : 'text-amber-400'}>{health.ai?.configured ? 'READY' : 'LOCAL'}</span>
              </span>
            </div>
          </div>
        </header>

        <main className="flex-1 min-h-0 relative bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-slate-800 via-slate-950 to-black overflow-hidden">
          {renderContent()}
        </main>
      </div>
    </div>
  );
}

export default App;
