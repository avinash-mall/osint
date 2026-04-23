import { useEffect, useState, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import axios from 'axios';

export default function GraphExplorer() {
  const [data, setData] = useState({ nodes: [], links: [] });
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

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
        
        // Ensure data is formatted correctly for react-force-graph
        if (response.data && response.data.nodes && response.data.links) {
           setData({
             nodes: response.data.nodes,
             links: response.data.links.map(link => ({
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

  return (
    <div className="w-full h-full relative" ref={containerRef}>
      <div className="absolute top-4 left-4 z-10 bg-slate-800/80 p-4 rounded border border-slate-700 backdrop-blur-sm shadow-lg">
        <h2 className="text-slate-200 font-semibold mb-2 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-blue-500"></span> Ontology
        </h2>
        <div className="text-xs text-slate-400 font-mono flex flex-col gap-1">
          <span>NODES: <span className="text-blue-400">{data.nodes.length}</span></span>
          <span>EDGES: <span className="text-blue-400">{data.links.length}</span></span>
        </div>
      </div>
      <ForceGraph2D
        width={dimensions.width}
        height={dimensions.height}
        graphData={data}
        nodeId="id"
        nodeLabel="label"
        nodeAutoColorBy="label"
        linkDirectionalArrowLength={3.5}
        linkDirectionalArrowRelPos={1}
        linkColor={() => '#475569'}
        backgroundColor="transparent"
        nodeCanvasObject={(node: any, ctx, globalScale) => {
          const label = node.properties?.name || node.label;
          const fontSize = 12/globalScale;
          ctx.font = `${fontSize}px Sans-Serif`;
          const textWidth = ctx.measureText(label).width;
          const bckgDimensions = [textWidth, fontSize].map(n => n + fontSize * 0.2);

          ctx.fillStyle = 'rgba(15, 23, 42, 0.8)';
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
  );
}
