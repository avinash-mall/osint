import { useEffect, useState } from 'react';
import axios from 'axios';
import { Target, AlertTriangle, CheckCircle, Eye, Edit2 } from 'lucide-react';

export default function TargetWorkbench() {
  const [targets, setTargets] = useState<any[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<any>(null);

  const fetchTargets = async () => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
      const response = await axios.get(`${apiUrl}/api/targets`);
      setTargets(response.data.targets || []);
    } catch (error) {
      console.error("Error fetching targets:", error);
    }
  };

  useEffect(() => {
    fetchTargets();
  }, []);

  const updateStatus = async (id: string, newStatus: string) => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
      await axios.put(`${apiUrl}/api/targets/${id}/status`, { status: newStatus });
      fetchTargets();
      if (selectedTarget && selectedTarget.id === id) {
         setSelectedTarget({...selectedTarget, properties: {...selectedTarget.properties, status: newStatus}});
      }
    } catch (error) {
      console.error("Error updating status:", error);
    }
  };

  const getPriorityColor = (priority: string) => {
    switch(priority?.toLowerCase()) {
      case 'high': return 'text-red-500 bg-red-500/20 border-red-500/50';
      case 'medium': return 'text-yellow-500 bg-yellow-500/20 border-yellow-500/50';
      case 'low': return 'text-blue-500 bg-blue-500/20 border-blue-500/50';
      default: return 'text-slate-400 bg-slate-800 border-slate-700';
    }
  };

  const getStatusIcon = (status: string) => {
    switch(status?.toLowerCase()) {
      case 'active': return <AlertTriangle className="w-4 h-4 text-red-500" />;
      case 'eliminated': return <CheckCircle className="w-4 h-4 text-emerald-500" />;
      case 'monitored': return <Eye className="w-4 h-4 text-blue-500" />;
      default: return <Target className="w-4 h-4 text-slate-500" />;
    }
  };

  return (
    <div className="w-full h-full bg-slate-950 flex flex-col md:flex-row overflow-hidden text-slate-200">
      
      {/* Main List Area */}
      <div className="flex-1 flex flex-col h-full overflow-hidden border-r border-slate-800">
        <div className="p-6 border-b border-slate-800 bg-slate-900 flex justify-between items-center">
          <div>
            <h2 className="text-xl font-bold tracking-wider uppercase flex items-center gap-3">
              <Target className="w-6 h-6 text-red-500" /> Target Workbench
            </h2>
            <p className="text-xs text-slate-500 font-mono mt-1">HPTL (High Priority Target List) Management</p>
          </div>
          <div className="flex items-center gap-4">
            <button 
              onClick={async () => {
                const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
                await axios.post(`${apiUrl}/api/ingest`, { image_url: 's3://satellites/usgs_pass_001.tif' });
                alert('Satellite ingestion pipeline triggered. Check back shortly for new targets.');
              }}
              className="px-3 py-1 bg-indigo-500/20 text-indigo-400 border border-indigo-500/50 rounded hover:bg-indigo-500/40 transition text-xs font-bold uppercase tracking-wider"
            >
              TRIGGER SATELLITE PASS
            </button>
            <div className="text-sm font-mono bg-slate-800 px-3 py-1 border border-slate-700 rounded">
              TOTAL: <span className="text-blue-400">{targets.length}</span>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-auto custom-scrollbar p-6">
          <div className="grid grid-cols-1 gap-2">
            {targets.map(target => (
              <div 
                key={target.id}
                onClick={() => setSelectedTarget(target)}
                className={`p-4 rounded border flex items-center justify-between cursor-pointer transition-all duration-200 ${
                  selectedTarget?.id === target.id 
                  ? 'bg-blue-900/30 border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.1)]' 
                  : 'bg-slate-900/50 border-slate-800 hover:bg-slate-800 hover:border-slate-600'
                }`}
              >
                <div className="flex items-center gap-4">
                  <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center border border-slate-700">
                    {getStatusIcon(target.properties.status)}
                  </div>
                  <div>
                    <div className="font-bold tracking-wide">{target.properties.name}</div>
                    <div className="text-xs font-mono text-slate-500">ID: {target.id}</div>
                  </div>
                </div>
                
                <div className="flex items-center gap-6">
                  <div className={`text-xs font-bold uppercase tracking-wider px-2 py-1 rounded border ${getPriorityColor(target.properties.priority)}`}>
                    {target.properties.priority || 'UNKNOWN'}
                  </div>
                  <div className="text-sm font-semibold w-24 text-right capitalize">
                    {target.properties.status}
                  </div>
                </div>
              </div>
            ))}
            
            {targets.length === 0 && (
              <div className="text-center text-slate-500 mt-20 font-mono">NO TARGETS FOUND</div>
            )}
          </div>
        </div>
      </div>

      {/* Detail Pane */}
      <div className={`w-96 bg-slate-900 h-full flex flex-col transition-all duration-300 transform ${selectedTarget ? 'translate-x-0' : 'translate-x-full absolute right-0'}`}>
        {selectedTarget && (
          <>
            <div className="p-6 border-b border-slate-800">
               <h3 className="text-sm font-bold text-slate-400 tracking-widest uppercase mb-4 flex items-center gap-2">
                 <Edit2 className="w-4 h-4" /> Target Details
               </h3>
               <div className="text-2xl font-bold mb-2">{selectedTarget.properties.name}</div>
               <div className={`inline-block text-xs font-bold uppercase tracking-wider px-2 py-1 rounded border mb-4 ${getPriorityColor(selectedTarget.properties.priority)}`}>
                  PRIORITY: {selectedTarget.properties.priority}
               </div>
            </div>

            <div className="p-6 flex-1 overflow-auto">
               <div className="mb-6">
                  <div className="text-xs text-slate-500 font-mono uppercase mb-2">Description</div>
                  <div className="bg-slate-950 p-4 rounded border border-slate-800 text-sm leading-relaxed text-slate-300">
                    {selectedTarget.properties.description || "No tactical description available."}
                  </div>
               </div>

               <div className="mb-6">
                  <div className="text-xs text-slate-500 font-mono uppercase mb-2">Update Status</div>
                  <div className="grid grid-cols-1 gap-2">
                     <button onClick={() => updateStatus(selectedTarget.id, 'Active')} className={`p-3 text-left rounded border flex items-center gap-3 transition-colors ${selectedTarget.properties.status === 'Active' ? 'bg-red-500/20 border-red-500 text-red-100' : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-600'}`}>
                        <AlertTriangle className={`w-5 h-5 ${selectedTarget.properties.status === 'Active' ? 'text-red-500' : ''}`} /> Active (Hostile)
                     </button>
                     <button onClick={() => updateStatus(selectedTarget.id, 'Monitored')} className={`p-3 text-left rounded border flex items-center gap-3 transition-colors ${selectedTarget.properties.status === 'Monitored' ? 'bg-blue-500/20 border-blue-500 text-blue-100' : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-600'}`}>
                        <Eye className={`w-5 h-5 ${selectedTarget.properties.status === 'Monitored' ? 'text-blue-500' : ''}`} /> Monitored
                     </button>
                     <button onClick={() => updateStatus(selectedTarget.id, 'Eliminated')} className={`p-3 text-left rounded border flex items-center gap-3 transition-colors ${selectedTarget.properties.status === 'Eliminated' ? 'bg-emerald-500/20 border-emerald-500 text-emerald-100' : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-600'}`}>
                        <CheckCircle className={`w-5 h-5 ${selectedTarget.properties.status === 'Eliminated' ? 'text-emerald-500' : ''}`} /> Eliminated
                     </button>
                  </div>
               </div>
            </div>
          </>
        )}
      </div>

    </div>
  );
}
