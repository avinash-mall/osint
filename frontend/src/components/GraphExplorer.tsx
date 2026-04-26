import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import axios from 'axios';
import { Search, Info, Activity, Maximize2, Share2, Filter } from 'lucide-react';

export default function GraphExplorer() {
  const [data, setData] = useState<any>({ nodes: [], links: [] });
  const [filteredData, setFilteredData] = useState<any | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number, y: number, node: any } | null>(null);

  useEffect(() => {
    const resizeObserver = new ResizeObserver(entries => {
      if (entries[0]) {
        setDimensions({
          width: entries[0].contentRect.width,
          height: entries[0].contentRect.height
        });
      }
    });

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
        const response = await axios.get(`${apiUrl}/api/graph`);
        
        if (response.data && response.data.nodes && response.data.links) {
           setData({
             nodes: response.data.nodes,
             links: response.data.links.map((link: any) => ({
               ...link,
               source: link.source,
               target: link.target
             }))
           });
        }
      } catch (error) {
        console.error("Error fetching graph data:", error);
      }
    };
    fetchData();
  }, []);

  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node);
    setContextMenu(null);
  }, []);

  const handleNodeRightClick = useCallback((node: any, event: any) => {
    setContextMenu({ x: event.clientX, y: event.clientY, node });
    setSelectedNode(node);
  }, []);

  const handleBackgroundClick = useCallback(() => {
    setContextMenu(null);
    setSelectedNode(null);
  }, []);

  const graphData = filteredData || data;

  const focusNode = useCallback((node: any) => {
    if (!graphRef.current || node.x === undefined || node.y === undefined) return;
    graphRef.current.centerAt(node.x, node.y, 700);
    graphRef.current.zoom(3, 700);
  }, []);

  const neighborhood = useCallback((node: any) => {
    const neighborIds = new Set<string>([node.id]);
    data.links.forEach((link: any) => {
      const source = typeof link.source === 'object' ? link.source.id : link.source;
      const target = typeof link.target === 'object' ? link.target.id : link.target;
      if (source === node.id) neighborIds.add(target);
      if (target === node.id) neighborIds.add(source);
    });
    setFilteredData({
      nodes: data.nodes.filter((item: any) => neighborIds.has(item.id)),
      links: data.links.filter((link: any) => {
        const source = typeof link.source === 'object' ? link.source.id : link.source;
        const target = typeof link.target === 'object' ? link.target.id : link.target;
        return neighborIds.has(source) && neighborIds.has(target);
      })
    });
    setContextMenu(null);
  }, [data]);

  const exportSelected = useCallback(() => {
    const payload = selectedNode || graphData;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = selectedNode ? `${selectedNode.label}-${selectedNode.id}.json` : 'graph-export.json';
    link.click();
    URL.revokeObjectURL(url);
  }, [graphData, selectedNode]);

  const labelIndex = useMemo<Map<string, any>>(() => new Map(data.nodes.map((node: any) => [node.id, node])), [data.nodes]);

  const expandNode = useCallback(async (node: any) => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8080';
      const response = await axios.post(`${apiUrl}/api/graph/neighborhood`, { node_id: node.id });
      setFilteredData(response.data);
      setContextMenu(null);
    } catch (error) {
      console.error("Error expanding node:", error);
      neighborhood(node);
    }
  }, [neighborhood]);

  return (
    <div className="w-full h-full relative overflow-hidden flex bg-slate-950" ref={containerRef} onClick={handleBackgroundClick}>
      {/* Graph Area */}
      <div className="flex-1 relative">
        <div className="absolute top-4 left-4 z-10 bg-slate-900/80 p-4 rounded border border-slate-700 backdrop-blur-sm shadow-lg">
          <h2 className="text-slate-200 font-semibold mb-2 flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-500" /> Link Analysis
          </h2>
          <div className="text-xs text-slate-400 font-mono flex flex-col gap-1">
            <span>ENTITIES: <span className="text-blue-400">{data.nodes.length}</span></span>
            <span>RELATIONS: <span className="text-blue-400">{data.links.length}</span></span>
          </div>
        </div>
        
        <ForceGraph2D
          width={selectedNode ? dimensions.width - 320 : dimensions.width}
          height={dimensions.height}
          ref={graphRef}
          graphData={graphData}
          nodeId="id"
          nodeLabel="label"
          nodeAutoColorBy="label"
          linkDirectionalArrowLength={3.5}
          linkDirectionalArrowRelPos={1}
          linkColor={() => '#334155'}
          backgroundColor="#020617"
          onNodeClick={handleNodeClick}
          onNodeRightClick={handleNodeRightClick}
          onBackgroundClick={handleBackgroundClick}
          nodeCanvasObject={(node: any, ctx, globalScale) => {
            const label = node.properties?.name || node.properties?.id || labelIndex.get(node.id)?.label || node.label;
            const isSelected = selectedNode && selectedNode.id === node.id;
            const fontSize = 12/globalScale;
            ctx.font = `${fontSize}px Sans-Serif`;
            const textWidth = ctx.measureText(label).width;
            const bckgDimensions = [textWidth, fontSize].map(n => n + fontSize * 0.2);

            if (isSelected) {
              ctx.beginPath();
              ctx.arc(node.x, node.y, 8, 0, 2 * Math.PI, false);
              ctx.fillStyle = 'rgba(59, 130, 246, 0.4)';
              ctx.fill();
              ctx.lineWidth = 2 / globalScale;
              ctx.strokeStyle = '#3b82f6';
              ctx.stroke();
            }

            ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
            ctx.fillRect(node.x - bckgDimensions[0] / 2, node.y - bckgDimensions[1] / 2, bckgDimensions[0], bckgDimensions[1]);

            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = node.color || '#3b82f6';
            ctx.fillText(label, node.x, node.y);
            
            node.__bckgDimensions = bckgDimensions;
          }}
          nodePointerAreaPaint={(node: any, color, ctx) => {
            ctx.fillStyle = color;
            const bckgDimensions = node.__bckgDimensions;
            bckgDimensions && ctx.fillRect(node.x - bckgDimensions[0] / 2, node.y - bckgDimensions[1] / 2, bckgDimensions[0], bckgDimensions[1]);
          }}
        />
      </div>

      {/* Properties Sidebar */}
      {selectedNode && (
        <div className="w-80 h-full bg-slate-900 border-l border-slate-800 flex flex-col z-20 shadow-xl overflow-hidden transition-all duration-300 transform translate-x-0" onClick={(e) => e.stopPropagation()}>
          <div className="p-4 border-b border-slate-800 bg-slate-800/50">
            <h3 className="text-sm font-bold text-slate-200 tracking-wider uppercase mb-1">Entity Details</h3>
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-blue-500/20 text-blue-400 border border-blue-500/30">
                {selectedNode.label}
              </span>
              <span className="text-xs text-slate-400 font-mono truncate">{selectedNode.id}</span>
            </div>
          </div>
          
          <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
            <h4 className="text-xs font-semibold text-slate-500 mb-3 flex items-center gap-1"><Info className="w-3 h-3" /> PROPERTIES</h4>
            <div className="flex flex-col gap-3">
              {Object.entries(selectedNode.properties || {}).map(([key, value]) => (
                <div key={key} className="bg-slate-800/30 p-2 rounded border border-slate-800">
                  <div className="text-[10px] text-slate-500 font-mono uppercase mb-1">{key}</div>
                  <div className="text-sm text-slate-200 break-words font-medium">{String(value)}</div>
                </div>
              ))}
            </div>
          </div>
          
          <div className="p-4 border-t border-slate-800 flex gap-2">
             <button onClick={() => focusNode(selectedNode)} className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs py-2 rounded border border-slate-700 transition flex items-center justify-center gap-1">
               <Maximize2 className="w-3 h-3" /> Focus
             </button>
             <button onClick={exportSelected} className="flex-1 bg-blue-600 hover:bg-blue-500 text-white text-xs py-2 rounded transition flex items-center justify-center gap-1">
               <Share2 className="w-3 h-3" /> Export
             </button>
          </div>
        </div>
      )}

      {/* Right Click Context Menu */}
      {contextMenu && (
        <div 
          className="absolute z-50 w-48 bg-slate-800 border border-slate-700 rounded shadow-2xl py-1 text-sm font-medium"
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-xs text-slate-400 font-mono border-b border-slate-700/50 mb-1 truncate">
            {contextMenu.node.properties?.name || contextMenu.node.id}
          </div>
          <button onClick={() => neighborhood(contextMenu.node)} className="w-full text-left px-4 py-2 text-slate-200 hover:bg-blue-600 hover:text-white flex items-center gap-2 transition">
            <Search className="w-4 h-4" /> Search Around
          </button>
          <button onClick={() => neighborhood(contextMenu.node)} className="w-full text-left px-4 py-2 text-slate-200 hover:bg-blue-600 hover:text-white flex items-center gap-2 transition">
            <Filter className="w-4 h-4" /> Add to Filter
          </button>
          <button onClick={() => expandNode(contextMenu.node)} className="w-full text-left px-4 py-2 text-slate-200 hover:bg-blue-600 hover:text-white flex items-center gap-2 transition">
            <Maximize2 className="w-4 h-4" /> Expand Node
          </button>
          {filteredData && (
            <button onClick={() => setFilteredData(null)} className="w-full text-left px-4 py-2 text-slate-400 hover:bg-slate-700 hover:text-white transition">
              Clear Filter
            </button>
          )}
        </div>
      )}
    </div>
  );
}
