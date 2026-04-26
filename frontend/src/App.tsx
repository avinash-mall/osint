import { useEffect, useState } from 'react';
import axios from 'axios';
import { Activity, Map as MapIcon, Database, MessageSquare, Hexagon, Target, Globe, Box, UploadCloud } from 'lucide-react';
import GraphExplorer from './components/GraphExplorer';
import GaiaMap from './components/GaiaMap';
import Browser from './components/Browser';
import AvaChat from './components/AvaChat';
import TargetWorkbench from './components/TargetWorkbench';
import ConstellationView from './components/ConstellationView';
import View3D from './components/View3D';
import IngestConnect from './components/IngestConnect';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

function App() {
  const [activeTab, setActiveTab] = useState('graph');
  const [health, setHealth] = useState<any>({ healthy: false, neo4j: 'unknown', postgis: 'unknown', ai: { configured: false } });

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/health`);
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
      case 'graph': return <GraphExplorer />;
      case 'map': return <GaiaMap />;
      case 'browser': return <Browser />;
      case 'ingest': return <IngestConnect />;
      case 'targets': return <TargetWorkbench />;
      case 'space': return <ConstellationView />;
      case 'ava': return <AvaChat />;
      case 'view3d': return <View3D />;
      default: return <GraphExplorer />;
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-900 text-gray-200 font-sans">
      {/* Sidebar - Titanium Client */}
      <div className="w-16 bg-slate-800 border-r border-slate-700 flex flex-col items-center py-4 space-y-8 z-20 shadow-[4px_0_24px_rgba(0,0,0,0.5)]">
        <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center mb-2 shadow-lg shadow-blue-500/30">
          <Hexagon className="text-white" size={24} fill="currentColor" />
        </div>
        
        <div className="flex flex-col space-y-4 w-full px-2">
          <button onClick={() => setActiveTab('graph')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'graph' ? 'bg-blue-500/20 text-blue-400 shadow-[inset_2px_0_0_0_#3b82f6]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Graph Explorer">
            <Activity size={22} />
          </button>
          <button onClick={() => setActiveTab('map')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'map' ? 'bg-blue-500/20 text-blue-400 shadow-[inset_2px_0_0_0_#3b82f6]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Gaia Map">
            <MapIcon size={22} />
          </button>
          <button onClick={() => setActiveTab('targets')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'targets' ? 'bg-blue-500/20 text-red-400 shadow-[inset_2px_0_0_0_#ef4444]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Target Workbench">
            <Target size={22} />
          </button>
          <button onClick={() => setActiveTab('space')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'space' ? 'bg-blue-500/20 text-indigo-400 shadow-[inset_2px_0_0_0_#818cf8]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Constellation">
            <Globe size={22} />
          </button>
          <button onClick={() => setActiveTab('browser')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'browser' ? 'bg-blue-500/20 text-blue-400 shadow-[inset_2px_0_0_0_#3b82f6]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Data Browser">
            <Database size={22} />
          </button>
          <button onClick={() => setActiveTab('ingest')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'ingest' ? 'bg-blue-500/20 text-emerald-400 shadow-[inset_2px_0_0_0_#10b981]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Ingest & Streams">
            <UploadCloud size={22} />
          </button>
          <button onClick={() => setActiveTab('view3d')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'view3d' ? 'bg-blue-500/20 text-cyan-400 shadow-[inset_2px_0_0_0_#22d3ee]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="3D View">
            <Box size={22} />
          </button>
          <button onClick={() => setActiveTab('ava')} className={`p-3 w-full flex justify-center rounded-xl transition-all duration-200 ${activeTab === 'ava' ? 'bg-emerald-500/20 text-emerald-400 shadow-[inset_2px_0_0_0_#10b981]' : 'text-slate-400 hover:bg-slate-700 hover:text-white'}`} title="Ava Assistant">
            <MessageSquare size={22} />
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 relative flex flex-col">
        {/* Header */}
        <header className="h-14 bg-slate-800/95 backdrop-blur border-b border-slate-700 flex items-center px-6 justify-between shadow-md z-10">
          <div className="flex items-center space-x-3">
            <h1 className="text-sm font-bold text-slate-100 tracking-widest uppercase">
              {activeTab === 'graph' && 'Titanium :: Ontology Explorer'}
              {activeTab === 'map' && 'Gaia :: Geospatial Platform'}
              {activeTab === 'targets' && 'TWB :: Target Workbench'}
              {activeTab === 'space' && 'Space :: Constellation Tracking'}
              {activeTab === 'browser' && 'Browser :: Raw Telemetry'}
              {activeTab === 'ingest' && 'Ingest :: Collections & Streams'}
              {activeTab === 'view3d' && 'Cesium :: 3D View'}
              {activeTab === 'ava' && 'Ava :: Cognitive Engine'}
            </h1>
          </div>
          <div className="flex items-center space-x-6 text-xs text-slate-400 font-mono">
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.healthy ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'}`}></span>
              <span>API <span className={health.healthy ? 'text-emerald-400' : 'text-red-400'}>{health.healthy ? 'ONLINE' : 'DEGRADED'}</span></span>
            </div>
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.neo4j === 'ok' ? 'bg-emerald-500' : 'bg-red-500'}`}></span>
              <span>ONTOLOGY <span className={health.neo4j === 'ok' ? 'text-emerald-400' : 'text-red-400'}>{health.neo4j === 'ok' ? 'SYNCED' : 'OFFLINE'}</span></span>
            </div>
            <div className="flex items-center space-x-2">
              <span className={`w-2 h-2 rounded-full ${health.ai?.configured ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
              <span>AVA <span className={health.ai?.configured ? 'text-emerald-400' : 'text-amber-400'}>{health.ai?.configured ? 'READY' : 'LOCAL'}</span></span>
            </div>
          </div>
        </header>
        
        {/* Workspace */}
        <main className="flex-1 relative bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-slate-800 via-slate-900 to-black overflow-hidden">
          {renderContent()}
        </main>
      </div>
    </div>
  );
}
export default App;
