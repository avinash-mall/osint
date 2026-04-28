import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import axios from 'axios';
import { Activity, Boxes, CircleDot, Database, Filter, GitBranch, Info, Maximize2, Network, Plus, Search, Share2, X } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

function nodeId(value: any): string {
  return typeof value === 'object' ? value.id : value;
}

function nodeTitle(node: any): string {
  const props = node?.properties || {};
  return String(props.name || props.label || props.id || props.class || node?.label || node?.id || 'Entity');
}

function nodeKind(node: any): string {
  const props = node?.properties || {};
  return String(props.entity_type || node?.label || 'entity').toLowerCase();
}

function kindColor(kind: string): string {
  if (kind.includes('target') || kind.includes('facility')) return '#ff3b30';
  if (kind.includes('detection') || kind.includes('candidate')) return '#ff7a1a';
  if (kind.includes('satellite') || kind.includes('pass')) return '#4ea1ff';
  if (kind.includes('asset') || kind.includes('vehicle') || kind.includes('aircraft')) return '#3dd68c';
  if (kind.includes('update') || kind.includes('document')) return '#a78bfa';
  return '#9bb1c4';
}

function NodeGlyph({ kind, size = 14 }: { kind: string; size?: number }) {
  const color = kindColor(kind);
  const Icon = kind.includes('detection') ? CircleDot : kind.includes('candidate') ? Plus : kind.includes('satellite') ? Activity : kind.includes('update') ? Database : Boxes;
  return (
    <span style={{ width: size + 6, height: size + 6, borderColor: color, color }} className="inline-flex items-center justify-center border bg-black/20">
      <Icon size={size - 1} />
    </span>
  );
}

export default function GraphExplorer() {
  const [data, setData] = useState<any>({ nodes: [], links: [] });
  const [filteredData, setFilteredData] = useState<any | null>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: any } | null>(null);
  const [showCandidateLinks, setShowCandidateLinks] = useState(false);
  const [query, setQuery] = useState('');
  const [updates, setUpdates] = useState<any[]>([]);
  const [dimensions, setDimensions] = useState({ width: 900, height: 600 });
  const graphPaneRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);

  const fetchData = useCallback(async () => {
    const [graphResponse, updatesResponse] = await Promise.all([
      axios.get(`${API_URL}/api/graph`, { params: { include_candidates: showCandidateLinks } }),
      axios.get(`${API_URL}/api/ontology/updates`, { params: { limit: 8 } }).catch(() => ({ data: { updates: [] } })),
    ]);
    setData({
      nodes: graphResponse.data.nodes || [],
      links: (graphResponse.data.links || []).map((link: any) => ({ ...link, source: link.source, target: link.target })),
    });
    setUpdates(updatesResponse.data.updates || []);
  }, [showCandidateLinks]);

  useEffect(() => {
    fetchData().catch((error) => console.error('Error fetching graph data:', error));
  }, [fetchData]);

  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      if (!entries[0]) return;
      setDimensions({
        width: Math.max(320, entries[0].contentRect.width),
        height: Math.max(320, entries[0].contentRect.height),
      });
    });
    if (graphPaneRef.current) observer.observe(graphPaneRef.current);
    return () => observer.disconnect();
  }, []);

  const graphData = filteredData || data;

  const nodeMap = useMemo(() => new Map(data.nodes.map((node: any) => [node.id, node])), [data.nodes]);

  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return data.nodes;
    return data.nodes.filter((node: any) => `${nodeTitle(node)} ${node.label} ${node.id}`.toLowerCase().includes(needle));
  }, [data.nodes, query]);

  const groupedNodes = useMemo(() => {
    return visibleNodes.reduce((groups: Record<string, any[]>, node: any) => {
      const kind = nodeKind(node);
      const group = kind.includes('target') ? 'target'
        : kind.includes('detection') ? 'detection'
          : kind.includes('candidate') ? 'candidate'
            : kind.includes('satellite') || kind.includes('pass') ? 'imagery'
              : kind.includes('update') || kind.includes('document') ? 'source'
                : kind || 'entity';
      groups[group] = groups[group] || [];
      groups[group].push(node);
      return groups;
    }, {});
  }, [visibleNodes]);

  const selectedConnections = useMemo(() => {
    if (!selectedNode) return [];
    return graphData.links.filter((link: any) => nodeId(link.source) === selectedNode.id || nodeId(link.target) === selectedNode.id);
  }, [graphData.links, selectedNode]);

  const density = useMemo(() => {
    const nodes = Math.max(1, graphData.nodes.length);
    return Math.min(1, graphData.links.length / Math.max(1, nodes * (nodes - 1))).toFixed(2);
  }, [graphData]);

  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node);
    setContextMenu(null);
  }, []);

  const handleNodeRightClick = useCallback((node: any, event: MouseEvent) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY, node });
    setSelectedNode(node);
  }, []);

  const focusNode = useCallback((node: any) => {
    if (!graphRef.current || node.x === undefined || node.y === undefined) return;
    graphRef.current.centerAt(node.x, node.y, 700);
    graphRef.current.zoom(3, 700);
  }, []);

  const neighborhood = useCallback((node: any) => {
    const neighborIds = new Set<string>([node.id]);
    data.links.forEach((link: any) => {
      const source = nodeId(link.source);
      const target = nodeId(link.target);
      if (source === node.id) neighborIds.add(target);
      if (target === node.id) neighborIds.add(source);
    });
    setFilteredData({
      nodes: data.nodes.filter((item: any) => neighborIds.has(item.id)),
      links: data.links.filter((link: any) => neighborIds.has(nodeId(link.source)) && neighborIds.has(nodeId(link.target))),
    });
    setContextMenu(null);
  }, [data]);

  const expandNode = useCallback(async (node: any) => {
    try {
      const response = await axios.post(`${API_URL}/api/graph/neighborhood`, { node_id: node.id });
      setFilteredData(response.data);
      setContextMenu(null);
    } catch {
      neighborhood(node);
    }
  }, [neighborhood]);

  const exportSelected = useCallback(() => {
    const payload = selectedNode || graphData;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = selectedNode ? `${nodeTitle(selectedNode)}-${selectedNode.id}.json` : 'graph-export.json';
    link.click();
    URL.revokeObjectURL(url);
  }, [graphData, selectedNode]);

  return (
    <div className="h-full min-h-0 grid grid-cols-[300px_minmax(0,1fr)_320px] bg-sentinel-bg text-sentinel-text overflow-hidden" onClick={() => setContextMenu(null)}>
      <aside className="sentinel-panel border-y-0 border-l-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          <GitBranch size={14} className="text-sentinel-accent" />
          <span>Entities · {data.nodes.length}</span>
          <div className="ml-auto sentinel-tag acc">{showCandidateLinks ? 'review' : 'approved'}</div>
        </div>
        <div className="p-2 border-b border-sentinel-line">
          <div className="h-8 flex items-center gap-2 border border-sentinel-line-2 bg-sentinel-bg px-2">
            <Search size={14} className="text-sentinel-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onClick={(event) => event.stopPropagation()}
              placeholder="search entity, class, id"
              className="min-w-0 flex-1 bg-transparent outline-none text-xs font-mono text-sentinel-text placeholder:text-sentinel-muted"
            />
            {query && <button type="button" onClick={() => setQuery('')} className="text-sentinel-muted"><X size={13} /></button>}
          </div>
        </div>
        <div className="sentinel-scroll flex-1">
          {(Object.entries(groupedNodes) as [string, any[]][]).map(([group, nodes]) => (
            <div key={group}>
              <div className="h-6 px-3 flex items-center border-b border-sentinel-line bg-sentinel-panel-2 text-[10px] uppercase tracking-[0.16em] text-sentinel-muted font-mono">
                {group} <span className="ml-auto">{nodes.length}</span>
              </div>
              {nodes.slice(0, 80).map((node) => {
                const selected = selectedNode?.id === node.id;
                const kind = nodeKind(node);
                return (
                  <button
                    type="button"
                    key={node.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedNode(node);
                      focusNode(node);
                    }}
                    className={`sentinel-row w-full text-left grid-cols-[24px_minmax(0,1fr)_auto] ${selected ? 'selected' : ''}`}
                  >
                    <NodeGlyph kind={kind} />
                    <span className="min-w-0">
                      <span className="block truncate text-xs text-sentinel-text">{nodeTitle(node)}</span>
                      <span className="block truncate text-[10px] text-sentinel-muted font-mono">{node.label}</span>
                    </span>
                    <span className="text-[10px] text-sentinel-muted font-mono">{String(node.id).slice(0, 5)}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </aside>

      <main className="sentinel-panel border-y-0 min-w-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          <Network size={14} className="text-sentinel-accent" />
          <span>Link Graph · 2-hop workspace</span>
          <div className="ml-auto flex items-center gap-2">
            <div className="flex border border-sentinel-line-2 h-6">
              <button className="px-3 bg-sentinel-accent text-sentinel-bg text-[10px] font-mono uppercase">Force</button>
              <button className="px-3 text-sentinel-muted text-[10px] font-mono uppercase border-l border-sentinel-line-2">Hier</button>
              <button className="px-3 text-sentinel-muted text-[10px] font-mono uppercase border-l border-sentinel-line-2">Geo</button>
            </div>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                setShowCandidateLinks((value) => !value);
              }}
              className={`sentinel-btn ${showCandidateLinks ? 'primary' : ''}`}
            >
              <Filter size={13} /> Candidates
            </button>
            {filteredData && (
              <button type="button" onClick={() => setFilteredData(null)} className="sentinel-btn">
                <X size={13} /> Clear
              </button>
            )}
          </div>
        </div>
        <div ref={graphPaneRef} className="relative flex-1 min-h-0 overflow-hidden bg-[#0a0d10]">
          <div className="sentinel-grid" />
          <ForceGraph2D
            width={dimensions.width}
            height={dimensions.height}
            ref={graphRef}
            graphData={graphData}
            nodeId="id"
            nodeLabel={nodeTitle}
            linkLabel={(link: any) => `${link.type}${link.score ? ` ${Number(link.score).toFixed(2)}` : ''}`}
            linkDirectionalArrowLength={3}
            linkDirectionalArrowRelPos={1}
            linkColor={(link: any) => link.candidate ? '#f5b400' : '#373e46'}
            linkLineDash={(link: any) => link.candidate ? [4, 4] : null}
            linkWidth={(link: any) => link.candidate ? 1.2 : 0.8}
            backgroundColor="rgba(0,0,0,0)"
            onNodeClick={handleNodeClick}
            onNodeRightClick={handleNodeRightClick as any}
            onBackgroundClick={() => {
              setSelectedNode(null);
              setContextMenu(null);
            }}
            nodeCanvasObject={(node: any, ctx, globalScale) => {
              const label = nodeTitle(node);
              const kind = nodeKind(node);
              const color = kindColor(kind);
              const isSelected = selectedNode?.id === node.id;
              const radius = isSelected ? 6 : 4;
              ctx.beginPath();
              ctx.arc(node.x, node.y, radius + (isSelected ? 5 : 0), 0, 2 * Math.PI, false);
              ctx.fillStyle = isSelected ? `${color}44` : '#0b0d0f';
              ctx.fill();
              ctx.lineWidth = isSelected ? 1.4 / globalScale : 0.8 / globalScale;
              ctx.strokeStyle = color;
              ctx.stroke();
              ctx.beginPath();
              ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
              ctx.fillStyle = color;
              ctx.fill();

              const fontSize = Math.max(9 / globalScale, 2.8);
              ctx.font = `${fontSize}px JetBrains Mono, monospace`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillStyle = isSelected ? '#e8ebee' : '#aab2bb';
              ctx.fillText(label.slice(0, 28), node.x, node.y - radius - fontSize);
              node.__bckgDimensions = [Math.max(ctx.measureText(label).width, 14), fontSize + radius * 2];
            }}
            nodePointerAreaPaint={(node: any, color, ctx) => {
              ctx.fillStyle = color;
              const dims = node.__bckgDimensions || [50, 18];
              ctx.fillRect(node.x - dims[0] / 2, node.y - dims[1], dims[0], dims[1] + 10);
            }}
          />
          <div className="absolute left-3 top-3 border border-sentinel-line bg-sentinel-panel/90 p-3 font-mono text-[10px] text-sentinel-muted">
            <div className="sentinel-label mb-2">Graph</div>
            <div>NODES <span className="text-sentinel-accent">{graphData.nodes.length}</span></div>
            <div>EDGES <span className="text-sentinel-accent">{graphData.links.length}</span></div>
            <div>DENSITY <span className="text-sentinel-accent">{density}</span></div>
          </div>
          <div className="absolute right-3 bottom-3 border border-sentinel-line bg-sentinel-panel/90 p-3 text-[10px] text-sentinel-muted">
            <div className="sentinel-label mb-2">Legend</div>
            {['target', 'detection', 'candidate', 'imagery', 'source', 'entity'].map((kind) => (
              <div key={kind} className="flex items-center gap-2 h-5">
                <span className="w-2 h-2 inline-block" style={{ background: kindColor(kind) }} />
                <span className="font-mono uppercase">{kind}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="h-20 border-t border-sentinel-line bg-sentinel-panel flex overflow-hidden">
          <div className="w-44 border-r border-sentinel-line p-3">
            <div className="sentinel-label">Ontology Updates</div>
            <div className="text-xl font-mono text-sentinel-accent">{updates.length}</div>
          </div>
          <div className="flex-1 overflow-x-auto flex">
            {updates.length ? updates.map((update) => (
              <div key={update.id} className="w-72 shrink-0 border-r border-sentinel-line p-2">
                <div className="flex items-center gap-2">
                  <span className={`sentinel-tag ${update.status === 'pending_review' ? 'warn' : update.status === 'unavailable' ? 'crit' : 'info'}`}>{update.status}</span>
                  <span className="text-[10px] text-sentinel-muted font-mono">#{update.id}</span>
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-sentinel-text">{update.summary || 'No summary generated.'}</div>
              </div>
            )) : <div className="p-3 text-sentinel-muted font-mono text-xs">No ontology proposals yet.</div>}
          </div>
        </div>
      </main>

      <aside className="sentinel-panel border-y-0 border-r-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          <Info size={14} className="text-sentinel-accent" />
          <span>Entity</span>
        </div>
        {selectedNode ? (
          <>
            <div className="p-4 border-b border-sentinel-line flex gap-3">
              <NodeGlyph kind={nodeKind(selectedNode)} size={22} />
              <div className="min-w-0">
                <div className="text-[10px] text-sentinel-muted font-mono uppercase truncate">{selectedNode.label} · {String(selectedNode.id).slice(0, 12)}</div>
                <div className="text-sm font-semibold truncate">{nodeTitle(selectedNode)}</div>
                <div className="mt-2 flex gap-1 flex-wrap">
                  <span className="sentinel-tag">deg {selectedConnections.length}</span>
                  {nodeKind(selectedNode).includes('candidate') && <span className="sentinel-tag warn">pending review</span>}
                </div>
              </div>
            </div>
            <div className="sentinel-scroll flex-1">
              <section className="p-3 border-b border-sentinel-line">
                <div className="sentinel-label mb-2">Connections</div>
                {selectedConnections.length ? selectedConnections.slice(0, 12).map((link: any, index: number) => {
                  const otherId = nodeId(link.source) === selectedNode.id ? nodeId(link.target) : nodeId(link.source);
                  const other = nodeMap.get(otherId);
                  return (
                    <div key={`${otherId}-${index}`} className="grid grid-cols-[22px_minmax(0,1fr)_52px] gap-2 items-center py-1.5">
                      <NodeGlyph kind={nodeKind(other)} size={13} />
                      <span className="truncate text-xs">{other ? nodeTitle(other) : otherId}</span>
                      <span className={`sentinel-tag ${link.candidate ? 'warn' : ''}`}>{link.candidate ? 'cand' : link.type}</span>
                    </div>
                  );
                }) : <div className="text-xs text-sentinel-muted font-mono">No visible adjacent links.</div>}
              </section>
              <section className="p-3">
                <div className="sentinel-label mb-2">Properties</div>
                <div className="space-y-2">
                  {Object.entries(selectedNode.properties || {}).slice(0, 24).map(([key, value]) => (
                    <div key={key} className="border border-sentinel-line bg-sentinel-bg p-2">
                      <div className="text-[10px] text-sentinel-muted font-mono uppercase">{key}</div>
                      <div className="text-xs text-sentinel-text break-words">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
            <div className="p-3 border-t border-sentinel-line flex gap-2">
              <button type="button" onClick={() => focusNode(selectedNode)} className="sentinel-btn flex-1 justify-center">
                <Maximize2 size={13} /> Focus
              </button>
              <button type="button" onClick={exportSelected} className="sentinel-btn primary flex-1 justify-center">
                <Share2 size={13} /> Export
              </button>
            </div>
          </>
        ) : (
          <div className="p-4 text-xs text-sentinel-muted font-mono">
            Select an entity or right-click a node for neighborhood actions.
          </div>
        )}
      </aside>

      {contextMenu && (
        <div
          className="fixed z-50 w-48 border border-sentinel-line-2 bg-sentinel-panel shadow-2xl py-1 text-xs"
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="px-3 py-2 text-[10px] text-sentinel-muted font-mono border-b border-sentinel-line truncate">
            {nodeTitle(contextMenu.node)}
          </div>
          <button type="button" onClick={() => neighborhood(contextMenu.node)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Search size={14} /> Search Around
          </button>
          <button type="button" onClick={() => expandNode(contextMenu.node)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Maximize2 size={14} /> Expand Node
          </button>
          <button type="button" onClick={exportSelected} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Share2 size={14} /> Export Selection
          </button>
          {filteredData && (
            <button type="button" onClick={() => setFilteredData(null)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 text-sentinel-muted">
              Clear Filter
            </button>
          )}
        </div>
      )}
    </div>
  );
}
