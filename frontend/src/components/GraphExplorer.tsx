import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import axios from 'axios';
import {
  Activity,
  Antenna,
  Boxes,
  Building2,
  CircleDot,
  Database,
  Filter,
  Gauge,
  Hash,
  Info,
  Layers,
  Maximize2,
  Plus,
  Radar,
  Route,
  Search,
  Share2,
  Ship,
  Smartphone,
  Sparkles,
  Truck,
  User,
  X,
} from 'lucide-react';
import { TimeScrubber, type TimeRange } from './graph/TimeScrubber';
import { EvidenceColumnDAG, type EvidencePayload } from './graph/EvidenceColumnDAG';
import { OntologyOrbit } from './graph/OntologyOrbit';

const API_URL = import.meta.env.VITE_API_URL || '';

type GraphMode = 'investigation' | 'evidence' | 'ontology';

const MODE_TABS: { id: GraphMode; label: string }[] = [
  { id: 'investigation', label: 'Investigation' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'ontology', label: 'Ontology' },
];

// Labels available in the class lens. Operational labels first, then evidence.
const CLASS_LENS_OPTIONS = [
  'Target', 'Asset', 'Base', 'LaunchPoint', 'Facility', 'Unit',
  'Vessel', 'Aircraft', 'Vehicle',
  'Detection', 'Observation', 'SatellitePass',
  'FMVClip', 'Document', 'Report', 'FeedEvent',
];

// Labels treated as "sites" — drives the "Roll up to site" context action.
const SITE_LABELS = new Set(['Base', 'LaunchPoint', 'Facility']);

function nodeId(value: any): string {
  return typeof value === 'object' ? value.id : value;
}

function nodeTitle(node: any): string {
  const props = node?.properties || {};
  return String(props.name || props.label || props.id || props.class || node?.label || node?.id || 'Entity');
}

function nodeKind(node: any): string {
  const props = node?.properties || {};
  const raw = String(props.entity_type || props.kind || props.type || node?.label || 'entity').toLowerCase();
  if (/facility|base|site|building|target/.test(raw)) return 'facility';
  if (/person|human|analyst|user/.test(raw)) return 'person';
  if (/vehicle|truck|car|aircraft|asset/.test(raw)) return 'vehicle';
  if (/vessel|ship|maritime/.test(raw)) return 'vessel';
  if (/phone|handset|mobile|imei|imsi|msisdn/.test(raw)) return 'phone';
  if (/account|alias|handle|user/.test(raw)) return 'account';
  if (/detection/.test(raw)) return 'detection';
  if (/candidate/.test(raw)) return 'candidate';
  if (/satellite|pass|imagery/.test(raw)) return 'imagery';
  if (/update|document|source/.test(raw)) return 'source';
  return raw || 'entity';
}

function kindColor(kind: string): string {
  if (kind === 'facility') return '#ff3b30';
  if (kind === 'person') return '#ff7a1a';
  if (kind === 'vehicle') return '#4ea1ff';
  if (kind === 'vessel') return '#3dd68c';
  if (kind === 'phone') return '#a78bfa';
  if (kind === 'account') return '#9bb1c4';
  if (kind === 'detection' || kind === 'candidate') return '#f5b400';
  if (kind === 'imagery') return '#4ea1ff';
  if (kind === 'source') return '#a78bfa';
  return '#9bb1c4';
}

function NodeGlyph({ kind, size = 14 }: { kind: string; size?: number }) {
  const color = kindColor(kind);
  const Icon = kind === 'facility' ? Building2
    : kind === 'person' ? User
      : kind === 'vehicle' ? Truck
        : kind === 'vessel' ? Ship
          : kind === 'phone' ? Smartphone
            : kind === 'account' ? Hash
              : kind === 'detection' ? CircleDot
                : kind === 'candidate' ? Plus
                  : kind === 'imagery' ? Activity
                    : kind === 'source' ? Database
                      : kind.includes('antenna') ? Antenna
                        : Boxes;
  return (
    <span style={{ width: size + 6, height: size + 6, borderColor: color, color }} className="inline-flex items-center justify-center border bg-black/20">
      <Icon size={size - 1} />
    </span>
  );
}

const GROUP_ORDER = ['facility', 'person', 'vehicle', 'vessel', 'phone', 'account', 'detection', 'candidate', 'imagery', 'source', 'entity'];

function linkScore(link: any): number | undefined {
  // The backend serialiser puts all relationship props under `link.properties`
  // and only lifts type/predicate/candidate to the top level, so the score
  // lives at link.properties.score (candidate-link scorer), not link.score.
  const p = link?.properties || {};
  const raw = Number(p.score ?? p.weight ?? p.confidence ?? link.score ?? link.weight ?? link.confidence);
  return Number.isFinite(raw) ? raw : undefined;
}

function linkWeight(link: any): number {
  const raw = linkScore(link);
  return raw === undefined ? 0.45 : Math.max(0.08, Math.min(1, raw));
}

// UX-AUDIT F22: edges carry a semantic predicate (the Neo4j relationship
// type, served as `predicate`). These helpers label and colour edges by it
// so the graph is an investigation surface, not "lots of dots".
function predicateOf(link: any): string {
  return String(link?.predicate || link?.type || 'related');
}

function predicateText(predicate: string): string {
  return predicate.replace(/^CANDIDATE_/, '').replace(/_/g, ' ').toUpperCase();
}

const PREDICATE_PALETTE = ['#5fc4ff', '#ffb14a', '#c87aff', '#5ee0a0', '#ff7a9c', '#9bd1ff', '#f0a020', '#7ad9c4'];

function predicateColor(predicate: string): string {
  let hash = 0;
  for (let i = 0; i < predicate.length; i += 1) hash = (hash * 31 + predicate.charCodeAt(i)) | 0;
  return PREDICATE_PALETTE[Math.abs(hash) % PREDICATE_PALETTE.length];
}

/** Predicate filter chip-row above the graph (UX-AUDIT F22). */
function PredicateChipBar({ predicates, enabled, onToggle }: {
  predicates: string[];
  enabled: Set<string>;
  onToggle: (predicate: string) => void;
}) {
  if (predicates.length === 0) return null;
  return (
    <div className="predicate-chip-bar" role="group" aria-label="Filter edges by predicate">
      {predicates.map((predicate) => {
        const on = enabled.has(predicate);
        const color = predicateColor(predicate);
        return (
          <button
            key={predicate}
            type="button"
            className={`predicate-chip ${on ? 'on' : ''}`}
            aria-pressed={on}
            onClick={() => onToggle(predicate)}
            style={on ? { color } : undefined}
          >
            <span className="predicate-swatch" style={{ background: on ? color : 'var(--line-2)' }} />
            {predicateText(predicate)}
          </button>
        );
      })}
    </div>
  );
}

/** Class-lens chip-row: restrict the Investigation feed to specific node labels. */
function ClassLensChipBar({ selected, onToggle, onClear }: {
  selected: Set<string>;
  onToggle: (label: string) => void;
  onClear: () => void;
}) {
  const anySelected = selected.size > 0;
  return (
    <div className="predicate-chip-bar" role="group" aria-label="Filter nodes by class">
      <span className="sentinel-label mr-1 self-center">Class lens</span>
      {CLASS_LENS_OPTIONS.map((label) => {
        const on = selected.has(label);
        return (
          <button
            key={label}
            type="button"
            className={`predicate-chip ${on ? 'on' : ''}`}
            aria-pressed={on}
            onClick={() => onToggle(label)}
          >
            {label}
          </button>
        );
      })}
      {anySelected && (
        <button type="button" className="predicate-chip" onClick={onClear} title="Clear class lens">
          <X size={11} />
        </button>
      )}
    </div>
  );
}

function defaultTimeRange(): TimeRange {
  const end = new Date();
  // Plan default: 30 days. Matches POL window.
  const start = new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

export default function GraphExplorer() {
  const [mode, setMode] = useState<GraphMode>('investigation');
  const [data, setData] = useState<any>({ nodes: [], links: [] });
  const [filteredData, setFilteredData] = useState<any | null>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: any } | null>(null);
  const [showCandidateLinks, setShowCandidateLinks] = useState(false);
  // Proximity (COLOCATED_WITH) and GNN (GNN_SUGGESTED_LINK) edges are dense /
  // advisory, so they start hidden — the chip surfaces them on demand, and the
  // co-location / suggest-links lenses re-enable them when explicitly invoked.
  const [disabledPredicates, setDisabledPredicates] = useState<Set<string>>(
    () => new Set(['COLOCATED_WITH', 'GNN_SUGGESTED_LINK']),
  );
  const [classLens, setClassLens] = useState<Set<string>>(new Set());
  const [timeRange, setTimeRange] = useState<TimeRange>(defaultTimeRange);
  const [query, setQuery] = useState('');
  const [updates, setUpdates] = useState<any[]>([]);
  const [dimensions, setDimensions] = useState({ width: 900, height: 600 });
  const [pathPicker, setPathPicker] = useState<{ from: any } | null>(null);
  const [pathResult, setPathResult] = useState<any | null>(null);
  const [siteRollup, setSiteRollup] = useState<any | null>(null);
  const [evidenceFocusId, setEvidenceFocusId] = useState<string | null>(null);
  const [evidencePayload, setEvidencePayload] = useState<EvidencePayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Phase 6: graph-analytics state (metrics card, co-location lens, GNN overlay).
  const [metrics, setMetrics] = useState<any | null>(null);
  const [gnnStatus, setGnnStatus] = useState<{ ready: boolean } | null>(null);
  const [gnnResult, setGnnResult] = useState<any | null>(null);
  const [gnnSuggestions, setGnnSuggestions] = useState<any[]>([]);
  const [lensBanner, setLensBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Phase 5.E: cluster keys ("${parentId}::${class}") the analyst has
  // manually expanded — those clusters render as their underlying nodes.
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const graphPaneRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);

  const fetchData = useCallback(async () => {
    setLoadError(null);
    if (mode !== 'investigation') {
      // Evidence and Ontology modes own their own fetch/render paths
      // (openEvidenceChain → EvidenceColumnDAG; OntologyOrbit fetches itself),
      // so the force-graph feed is only populated for Investigation mode.
      setData({ nodes: [], links: [] });
      setUpdates([]);
      return;
    }
    try {
      const params: Record<string, any> = {
        time_start: timeRange.start,
        time_end: timeRange.end,
        limit: 150,
      };
      if (classLens.size) {
        params.class_lens = Array.from(classLens);
      }
      const [graphResponse, updatesResponse] = await Promise.all([
        axios.get(`${API_URL}/api/graph/investigation`, {
          params,
          paramsSerializer: { indexes: null },  // class_lens=A&class_lens=B
        }),
        axios.get(`${API_URL}/api/ontology/updates`, { params: { limit: 8 } }).catch(() => ({ data: { updates: [] } })),
      ]);
      setData({
        nodes: graphResponse.data.nodes || [],
        links: (graphResponse.data.links || []).map((link: any) => ({ ...link, source: link.source, target: link.target })),
      });
      setUpdates(updatesResponse.data.updates || []);
    } catch (err: any) {
      console.error('Error fetching graph data:', err);
      setLoadError(err?.message || 'Failed to load graph');
    }
  }, [mode, timeRange.start, timeRange.end, classLens]);

  useEffect(() => {
    fetchData().catch((error) => console.error('Error fetching graph data:', error));
  }, [fetchData]);

  // A: pull graph-level metrics + centrality for the metrics card and the
  // centrality-weighted node sizing. Scoped to Investigation; candidate edges
  // included to mirror the visible topology.
  const fetchMetrics = useCallback(async () => {
    if (mode !== 'investigation') { setMetrics(null); return; }
    try {
      const resp = await axios.get(`${API_URL}/api/graph/metrics`, {
        params: { include_candidates: showCandidateLinks, limit: 1500, top_k: 50 },
      });
      setMetrics(resp.data);
    } catch (err) {
      console.error('graph metrics fetch failed', err);
      setMetrics(null);
    }
  }, [mode, showCandidateLinks]);

  useEffect(() => {
    fetchMetrics().catch((error) => console.error('graph metrics fetch failed', error));
  }, [fetchMetrics]);

  // C: probe whether the GNN runtime is installed so the "Suggest links"
  // control can gate itself (honest disabled state, like the map's DEM/OSRM).
  useEffect(() => {
    let cancelled = false;
    axios.get(`${API_URL}/api/graph/gnn/status`)
      .then((resp) => { if (!cancelled) setGnnStatus({ ready: Boolean(resp.data?.ready) }); })
      .catch(() => { if (!cancelled) setGnnStatus({ ready: false }); });
    return () => { cancelled = true; };
  }, []);

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

  // UX-AUDIT F22 — predicate set + filter folded into the rendered graph.
  const availablePredicates = useMemo(() => {
    const set = new Set<string>();
    data.links.forEach((link: any) => set.add(predicateOf(link)));
    return Array.from(set).sort();
  }, [data.links]);

  const enabledPredicates = useMemo(
    () => new Set(availablePredicates.filter((p) => !disabledPredicates.has(p))),
    [availablePredicates, disabledPredicates],
  );

  const togglePredicate = useCallback((predicate: string) => {
    setDisabledPredicates((cur) => {
      const next = new Set(cur);
      if (next.has(predicate)) next.delete(predicate);
      else next.add(predicate);
      return next;
    });
  }, []);

  const toggleClassLens = useCallback((label: string) => {
    setClassLens((cur) => {
      const next = new Set(cur);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  // Histogram source for the time scrubber: every node with a `created_at`
  // property (Detections, candidates, ontology updates).
  const histogramTimestamps = useMemo(() => {
    const out: number[] = [];
    for (const node of data.nodes || []) {
      const ts = node?.properties?.created_at;
      if (!ts) continue;
      const ms = Date.parse(ts);
      if (Number.isFinite(ms)) out.push(ms);
    }
    return out;
  }, [data.nodes]);

  // A: centrality scores keyed by node id (Neo4j elementId), merged across the
  // three measures the metrics endpoint returns. Drives node sizing + the card.
  const centrality = useMemo(() => {
    const map = new Map<string, { pagerank: number; betweenness: number; degree: number }>();
    const tc = metrics?.top_centrality;
    if (!tc) return { map, maxPagerank: 0 };
    const merge = (arr: any[], key: 'pagerank' | 'betweenness' | 'degree') => {
      (arr || []).forEach((e: any) => {
        const cur = map.get(e.id) || { pagerank: 0, betweenness: 0, degree: 0 };
        cur[key] = Number(e.score) || 0;
        map.set(e.id, cur);
      });
    };
    merge(tc.pagerank, 'pagerank');
    merge(tc.betweenness, 'betweenness');
    merge(tc.degree, 'degree');
    let maxPagerank = 0;
    for (const v of map.values()) maxPagerank = Math.max(maxPagerank, v.pagerank);
    return { map, maxPagerank };
  }, [metrics]);

  // Phase 5.E: collapse dense same-class neighbourhoods into a single
  // :Cluster virtual node. Threshold matches the plan (≥12).
  const CLUSTER_THRESHOLD = 12;

  const graphData = useMemo(() => {
    const base = filteredData || data;

    // C: fold GNN suggestion edges into the base graph, but only those whose
    // both endpoints are in the current view (the model snapshots a wider slice
    // than the time/class-scoped feed). They carry predicate GNN_SUGGESTED_LINK
    // so they ride the normal predicate filter / colour path.
    const baseNodeIds = new Set(base.nodes.map((n: any) => n.id));
    const gnnLinks = gnnSuggestions
      .filter((s: any) => baseNodeIds.has(s.source) && baseNodeIds.has(s.target))
      .map((s: any) => ({
        source: s.source,
        target: s.target,
        type: 'GNN_SUGGESTED_LINK',
        predicate: 'GNN_SUGGESTED_LINK',
        score: s.score,
        properties: { score: s.score },
        __gnn: true,
      }));

    // First pass: predicate + candidate filtering (unchanged from prior).
    let nodes = base.nodes;
    let links = [...base.links, ...gnnLinks];
    if (!showCandidateLinks) {
      links = links.filter((link: any) => !String(predicateOf(link)).startsWith('CANDIDATE_'));
    }
    if (disabledPredicates.size > 0) {
      links = links.filter((link: any) => !disabledPredicates.has(predicateOf(link)));
    }

    // Second pass (Phase 5.E): for each node, count same-class neighbours
    // among the remaining edges. When count >= threshold AND the cluster
    // isn't manually expanded, replace those neighbours with one virtual
    // :Cluster node and the 12+ edges with one.
    const idToLabel = new Map<string, string>();
    for (const n of nodes) idToLabel.set(n.id, n.label || 'Node');

    // adjacency[nodeId] = [{neighborId, link}]
    const adjacency = new Map<string, { neighborId: string; link: any }[]>();
    for (const link of links) {
      const s = nodeId(link.source); const t = nodeId(link.target);
      adjacency.set(s, [...(adjacency.get(s) || []), { neighborId: t, link }]);
      adjacency.set(t, [...(adjacency.get(t) || []), { neighborId: s, link }]);
    }

    // Group neighbours of each node by their class label.
    const clustersToBuild: Array<{
      parentId: string; cls: string;
      memberIds: Set<string>; memberLinks: any[];
    }> = [];
    const seenClusterKeys = new Set<string>();
    for (const parent of nodes) {
      const neighbours = adjacency.get(parent.id) || [];
      const byClass = new Map<string, { neighborId: string; link: any }[]>();
      for (const nb of neighbours) {
        const cls = idToLabel.get(nb.neighborId) || 'Node';
        byClass.set(cls, [...(byClass.get(cls) || []), nb]);
      }
      for (const [cls, members] of byClass) {
        if (members.length < CLUSTER_THRESHOLD) continue;
        const key = `${parent.id}::${cls}`;
        if (expandedClusters.has(key) || seenClusterKeys.has(key)) continue;
        seenClusterKeys.add(key);
        clustersToBuild.push({
          parentId: parent.id, cls,
          memberIds: new Set(members.map((m) => m.neighborId)),
          memberLinks: members.map((m) => m.link),
        });
      }
    }

    if (clustersToBuild.length === 0) {
      return { nodes, links };
    }

    // Determine which member nodes are *only* held in by-this-cluster edges
    // (i.e., they aren't simultaneously connected to other parents). Those
    // can safely be hidden; nodes that anchor multiple clusters stay visible.
    const memberToClusters = new Map<string, Set<string>>();
    for (const c of clustersToBuild) {
      for (const mid of c.memberIds) {
        const key = `${c.parentId}::${c.cls}`;
        memberToClusters.set(mid, new Set([...(memberToClusters.get(mid) || []), key]));
      }
    }
    // A member is hideable only if every adjacency it has goes to the same
    // parent via the same class. Otherwise leave the node visible.
    const hiddenIds = new Set<string>();
    for (const mid of memberToClusters.keys()) {
      const nbs = adjacency.get(mid) || [];
      const allAccounted = nbs.every(({ neighborId }) => {
        // edge to the parent of one of this member's clusters?
        for (const c of clustersToBuild) {
          if (c.memberIds.has(mid) && (neighborId === c.parentId)) return true;
        }
        return false;
      });
      if (allAccounted) hiddenIds.add(mid);
    }

    // Build the virtual cluster nodes + replacement edges.
    const clusterNodes: any[] = [];
    const clusterLinks: any[] = [];
    const collapsedLinkIds = new Set<any>();
    for (const c of clustersToBuild) {
      const clusterId = `${c.parentId}:cluster:${c.cls}`;
      clusterNodes.push({
        id: clusterId,
        label: 'Cluster',
        labels: ['Cluster'],
        properties: {
          parent_id: c.parentId,
          class: c.cls,
          count: c.memberIds.size,
        },
        __cluster: true,
        __cluster_key: `${c.parentId}::${c.cls}`,
      });
      clusterLinks.push({
        source: c.parentId,
        target: clusterId,
        type: 'CONTAINS_CLUSTER',
        predicate: 'CONTAINS_CLUSTER',
        properties: { count: c.memberIds.size },
      });
      for (const l of c.memberLinks) collapsedLinkIds.add(l);
    }

    const filteredNodes = nodes.filter((n: any) => !hiddenIds.has(n.id));
    const filteredLinks = links.filter((l: any) => !collapsedLinkIds.has(l));

    return {
      nodes: [...filteredNodes, ...clusterNodes],
      links: [...filteredLinks, ...clusterLinks],
    };
  }, [filteredData, data, disabledPredicates, showCandidateLinks, expandedClusters, gnnSuggestions]);

  const nodeMap = useMemo(() => new Map(data.nodes.map((node: any) => [node.id, node])), [data.nodes]);

  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return data.nodes;
    return data.nodes.filter((node: any) => `${nodeTitle(node)} ${node.label} ${node.id}`.toLowerCase().includes(needle));
  }, [data.nodes, query]);

  const groupedNodes = useMemo(() => {
    const groups = visibleNodes.reduce((acc: Record<string, any[]>, node: any) => {
      const kind = nodeKind(node);
      const group = GROUP_ORDER.includes(kind) ? kind : 'entity';
      acc[group] = acc[group] || [];
      acc[group].push(node);
      return acc;
    }, {});
    return GROUP_ORDER
      .filter((group) => groups[group]?.length)
      .map((group) => [group, groups[group]] as [string, any[]]);
  }, [visibleNodes]);

  const selectedConnections = useMemo(() => {
    if (!selectedNode) return [];
    return graphData.links.filter((link: any) => nodeId(link.source) === selectedNode.id || nodeId(link.target) === selectedNode.id);
  }, [graphData.links, selectedNode]);

  const density = useMemo(() => {
    const nodes = Math.max(1, graphData.nodes.length);
    return Math.min(1, graphData.links.length / Math.max(1, nodes * (nodes - 1))).toFixed(2);
  }, [graphData]);

  const selectedNeighborIds = useMemo(() => {
    const ids = new Set<string>();
    if (!selectedNode) return ids;
    ids.add(selectedNode.id);
    selectedConnections.forEach((link: any) => {
      ids.add(nodeId(link.source));
      ids.add(nodeId(link.target));
    });
    return ids;
  }, [selectedConnections, selectedNode]);

  // Real co-occurrence histogram: bucket the selected node's neighbours by
  // their `created_at` across the active time window. Shows *when* the
  // entities linked to this node appeared — derived from the graph itself,
  // no synthetic data.
  const cooccurrenceBars = useMemo(() => {
    const BUCKETS = 8;
    const counts = Array.from({ length: BUCKETS }, () => 0);
    if (!selectedNode) return { counts, total: 0 };
    const startMs = Date.parse(timeRange.start);
    const endMs = Date.parse(timeRange.end);
    const span = endMs - startMs;
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || span <= 0) {
      return { counts, total: 0 };
    }
    let total = 0;
    for (const link of selectedConnections) {
      const otherId = nodeId(link.source) === selectedNode.id ? nodeId(link.target) : nodeId(link.source);
      const other = nodeMap.get(otherId);
      const ts = (other as any)?.properties?.created_at;
      const ms = ts ? Date.parse(ts) : NaN;
      if (!Number.isFinite(ms)) continue;
      const idx = Math.min(BUCKETS - 1, Math.max(0, Math.floor(((ms - startMs) / span) * BUCKETS)));
      counts[idx] += 1;
      total += 1;
    }
    return { counts, total };
  }, [selectedNode, selectedConnections, nodeMap, timeRange.start, timeRange.end]);

  const handleNodeClick = useCallback((node: any) => {
    // Phase 5.E: clicking a cluster expands it back into its member nodes.
    if (node?.__cluster && node?.__cluster_key) {
      setExpandedClusters((cur) => {
        const next = new Set(cur);
        next.add(String(node.__cluster_key));
        return next;
      });
      return;
    }
    setSelectedNode(node);
    setContextMenu(null);
    setPathResult(null);
    setSiteRollup(null);
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

  const startPathPick = useCallback((node: any) => {
    setPathPicker({ from: node });
    setContextMenu(null);
    setPathResult(null);
  }, []);

  const completePath = useCallback(async (toNode: any) => {
    if (!pathPicker?.from) return;
    try {
      const resp = await axios.post(`${API_URL}/api/graph/path`, {
        from_id: pathPicker.from.id,
        to_id: toNode.id,
        max_depth: 4,
      });
      setPathResult({
        from: pathPicker.from,
        to: toNode,
        ...resp.data,
      });
      // Replace the visible graph with the union of all paths returned.
      const allNodes = new Map<string, any>();
      const allLinks: any[] = [];
      for (const p of resp.data.paths || []) {
        for (const n of p.nodes || []) allNodes.set(n.id, n);
        for (const l of p.links || []) allLinks.push(l);
      }
      if (allNodes.size > 0) {
        setFilteredData({ nodes: Array.from(allNodes.values()), links: allLinks });
      }
    } catch (err) {
      console.error('path query failed', err);
      setPathResult({ from: pathPicker.from, to: toNode, paths: [], count: 0, error: 'request failed' });
    } finally {
      setPathPicker(null);
    }
  }, [pathPicker]);

  const cancelPath = useCallback(() => setPathPicker(null), []);

  // Evidence chain action: switches mode to evidence, fetches the chain.
  const openEvidenceChain = useCallback(async (node: any) => {
    setContextMenu(null);
    setMode('evidence');
    setEvidenceFocusId(node.id);
    setEvidencePayload(null);
    try {
      const resp = await axios.get(`${API_URL}/api/graph/evidence/${encodeURIComponent(node.id)}`);
      setEvidencePayload(resp.data);
    } catch (err) {
      console.error('evidence fetch failed', err);
      setEvidencePayload({
        focus: { id: node.id, label: node.label, properties: node.properties || {} },
        nodes: [], links: [],
        evidence_records: {},
      } as EvidencePayload);
    }
  }, []);

  const contradictDetection = useCallback(async (actorId: string, detectionPostgisId: number) => {
    try {
      await axios.post(`${API_URL}/api/graph/contradict`, {
        actor_id: actorId,
        detection_postgis_id: detectionPostgisId,
      });
    } catch (err) {
      console.error('contradict failed', err);
    }
  }, []);

  const rollupSite = useCallback(async (node: any) => {
    setContextMenu(null);
    setPathResult(null);
    try {
      const resp = await axios.get(`${API_URL}/api/graph/site-composition/${encodeURIComponent(node.id)}`);
      setSiteRollup(resp.data);
    } catch (err) {
      console.error('site-composition failed', err);
      setSiteRollup({ error: 'request failed', base_id: node.id });
    }
  }, []);

  // B: co-location lens — render the live proximity graph of recent detections
  // as a filtered view. Detections are keyed by PostGIS id (a separate id space
  // from the Neo4j entity feed), so this replaces the canvas rather than merging.
  const runColocationLens = useCallback(async () => {
    setBusy('coloc');
    try {
      const startMs = Date.parse(timeRange.start);
      const endMs = Date.parse(timeRange.end);
      const windowDays = Math.max(1, Math.min(3650, Math.round((endMs - startMs) / 86_400_000)));
      const resp = await axios.get(`${API_URL}/api/graph/colocation`, {
        params: { method: 'knn', k: 6, radius_m: 3000, window_days: windowDays, limit: 1500 },
      });
      const nodes = (resp.data.nodes || []).map((n: any) => ({
        id: `det-${n.id}`,
        label: 'Detection',
        labels: ['Detection'],
        properties: { detection_id: n.id, longitude: n.lon, latitude: n.lat },
      }));
      const links = (resp.data.edges || []).map((e: any) => ({
        source: `det-${e.source}`,
        target: `det-${e.target}`,
        type: 'COLOCATED_WITH',
        predicate: 'COLOCATED_WITH',
        properties: { distance_m: e.distance_m },
      }));
      setDisabledPredicates((cur) => { const next = new Set(cur); next.delete('COLOCATED_WITH'); return next; });
      setSelectedNode(null); setPathResult(null); setSiteRollup(null); setGnnResult(null);
      setFilteredData({ nodes, links });
      setLensBanner(`Co-location lens · ${resp.data.method} · ${nodes.length} detections · ${links.length} edges`);
    } catch (err) {
      console.error('colocation lens failed', err);
      setLensBanner('Co-location lens failed');
    } finally {
      setBusy(null);
    }
  }, [timeRange.start, timeRange.end]);

  // C: run GNN link prediction and overlay the suggested operational-entity
  // pairs as advisory dashed edges. Gated on gnnStatus.ready (torch installed).
  const runGnnSuggest = useCallback(async () => {
    if (!gnnStatus?.ready) return;
    setBusy('gnn');
    try {
      const resp = await axios.post(`${API_URL}/api/graph/gnn/suggest-links`, { limit: 1000, top_k: 25 });
      setGnnSuggestions(resp.data.suggestions || []);
      setGnnResult(resp.data);
      setDisabledPredicates((cur) => { const next = new Set(cur); next.delete('GNN_SUGGESTED_LINK'); return next; });
      setPathResult(null); setSiteRollup(null);
    } catch (err: any) {
      console.error('gnn suggest failed', err);
      setGnnResult({ suggestions: [], error: err?.response?.data?.detail || 'request failed' });
    } finally {
      setBusy(null);
    }
  }, [gnnStatus]);

  const clearGnnOverlay = useCallback(() => {
    setGnnSuggestions([]);
    setGnnResult(null);
  }, []);

  // A: select a node by id from the metrics card (find it in the current feed).
  const selectNodeById = useCallback((id: string) => {
    const node = data.nodes.find((n: any) => n.id === id);
    if (node) {
      setSelectedNode(node);
      setContextMenu(null);
      requestAnimationFrame(() => focusNode(node));
    }
  }, [data.nodes, focusNode]);

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

  const selectedIsSite = useMemo(() => {
    if (!contextMenu?.node) return false;
    const labels = new Set<string>([contextMenu.node.label, ...(contextMenu.node.labels || [])]);
    for (const l of labels) if (SITE_LABELS.has(l)) return true;
    return false;
  }, [contextMenu]);

  return (
    <div className="graph-shell h-full min-h-0 bg-sentinel-bg text-sentinel-text overflow-hidden" onClick={() => setContextMenu(null)}>
      <aside className="graph-entity-panel sentinel-panel border-y-0 border-l-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          {/* UX-AUDIT F23: Share2 reads as "link network", not "target lock". */}
          <Share2 size={14} className="text-sentinel-accent" />
          <span>Entities · {data.nodes.length}</span>
          <button type="button" onClick={fetchData} className="sentinel-icon-btn ml-auto h-6 w-6">
            <Plus size={13} />
          </button>
        </div>
        <div className="p-2 border-b border-sentinel-line">
          <div className="h-8 flex items-center gap-2 border border-sentinel-line-2 bg-sentinel-bg px-2">
            <Search size={14} className="text-sentinel-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onClick={(event) => event.stopPropagation()}
              placeholder="search entity, phone, alias..."
              className="min-w-0 flex-1 bg-transparent outline-none text-xs font-mono text-sentinel-text placeholder:text-sentinel-muted"
            />
            {query && <button type="button" onClick={() => setQuery('')} className="text-sentinel-muted"><X size={13} /></button>}
          </div>
        </div>
        <div className="sentinel-scroll flex-1">
          {groupedNodes.map(([group, nodes]) => (
            <div key={group}>
              <div className="h-6 px-3 flex items-center border-b border-sentinel-line bg-sentinel-panel-2 text-[10px] uppercase tracking-[0.16em] font-mono" style={{ color: kindColor(group) }}>
                {group} <span className="ml-auto text-sentinel-muted">{nodes.length}</span>
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
                      // If we're picking the second node of a path, complete that.
                      if (pathPicker) {
                        completePath(node);
                      } else {
                        setSelectedNode(node);
                        focusNode(node);
                      }
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

      <main className="graph-main-panel sentinel-panel border-y-0 min-w-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          <span>Link Graph · {MODE_TABS.find((m) => m.id === mode)?.label}</span>
          <div className="ml-auto flex items-center gap-2">
            <div className="flex border border-sentinel-line-2 h-6" role="tablist" aria-label="Graph mode">
              {MODE_TABS.map((tab) => {
                const on = mode === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={on}
                    onClick={(event) => {
                      event.stopPropagation();
                      setMode(tab.id);
                      setFilteredData(null);
                      setPathResult(null);
                      setSiteRollup(null);
                      // Clear cross-mode state so switching tabs never shows a
                      // stale evidence chain or a selection from another mode.
                      setSelectedNode(null);
                      setContextMenu(null);
                      setEvidencePayload(null);
                      setEvidenceFocusId(null);
                    }}
                    className={`px-3 text-[10px] font-mono uppercase whitespace-nowrap border-l first:border-l-0 border-sentinel-line-2 ${
                      on ? 'bg-sentinel-accent text-sentinel-bg' : 'text-sentinel-muted'
                    }`}
                  >
                    {tab.label}
                  </button>
                );
              })}
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
            <span className="sentinel-tag acc">{showCandidateLinks ? 'review' : 'approved'}</span>
            {mode === 'investigation' && (
              <>
                <button
                  type="button"
                  onClick={(event) => { event.stopPropagation(); runColocationLens(); }}
                  disabled={busy === 'coloc'}
                  className="sentinel-btn"
                  title="Build the live proximity (co-location) graph of recent detections"
                >
                  <Radar size={13} /> {busy === 'coloc' ? 'Co-loc…' : 'Co-loc'}
                </button>
                <button
                  type="button"
                  onClick={(event) => { event.stopPropagation(); if (gnnStatus?.ready) runGnnSuggest(); }}
                  disabled={!gnnStatus?.ready || busy === 'gnn'}
                  className={`sentinel-btn ${gnnSuggestions.length ? 'primary' : ''}`}
                  title={gnnStatus?.ready
                    ? 'Predict missing entity links with the GraphSAGE GNN'
                    : 'GNN runtime not installed (torch absent in this image)'}
                >
                  <Sparkles size={13} /> {busy === 'gnn' ? 'Predicting…' : 'Suggest links'}
                </button>
                {gnnSuggestions.length > 0 && (
                  <button type="button" onClick={(event) => { event.stopPropagation(); clearGnnOverlay(); }} className="sentinel-btn" title="Clear GNN overlay">
                    <X size={13} /> GNN
                  </button>
                )}
              </>
            )}
            {filteredData && (
              <button type="button" onClick={() => { setFilteredData(null); setPathResult(null); setLensBanner(null); }} className="sentinel-btn">
                <X size={13} /> Clear
              </button>
            )}
            <button type="button" onClick={() => selectedNode && focusNode(selectedNode)} className="sentinel-icon-btn h-6 w-6">
              <Maximize2 size={13} />
            </button>
          </div>
        </div>

        {mode === 'investigation' && (
          <>
            <div className="px-2 pt-2 grid grid-cols-[minmax(0,1fr)_minmax(220px,320px)] gap-2">
              <ClassLensChipBar
                selected={classLens}
                onToggle={toggleClassLens}
                onClear={() => setClassLens(new Set())}
              />
              <TimeScrubber
                value={timeRange}
                onChange={setTimeRange}
                histogramTimestamps={histogramTimestamps}
              />
            </div>
            <PredicateChipBar
              predicates={availablePredicates}
              enabled={enabledPredicates}
              onToggle={togglePredicate}
            />
            {pathPicker && (
              <div className="px-3 py-1.5 text-[11px] font-mono bg-sentinel-accent text-sentinel-bg flex items-center gap-2">
                <Route size={13} />
                Path from <strong>{nodeTitle(pathPicker.from)}</strong> to … click the second node.
                <button type="button" onClick={cancelPath} className="ml-auto sentinel-btn">
                  <X size={13} /> Cancel
                </button>
              </div>
            )}
            {loadError && (
              <div className="px-3 py-1.5 text-[11px] font-mono bg-sentinel-warning/20 text-sentinel-warning">
                {loadError}
              </div>
            )}
            {lensBanner && (
              <div className="px-3 py-1.5 text-[11px] font-mono bg-sentinel-accent/15 text-sentinel-accent flex items-center gap-2">
                <Radar size={13} /> {lensBanner}
                <button type="button" onClick={() => { setFilteredData(null); setLensBanner(null); }} className="ml-auto sentinel-btn">
                  <X size={13} /> Exit lens
                </button>
              </div>
            )}
          </>
        )}

        <div ref={graphPaneRef} className="relative flex-1 min-h-0 overflow-hidden bg-[#0a0d10]">
          <div className="absolute inset-0 opacity-70" style={{ backgroundImage: 'radial-gradient(circle, #1d2227 1px, transparent 1px)', backgroundSize: '22px 22px' }} />
          {mode === 'evidence' ? (
            evidencePayload ? (
              <EvidenceColumnDAG
                payload={evidencePayload}
                onContradict={contradictDetection}
                onClose={() => {
                  setMode('investigation');
                  setEvidenceFocusId(null);
                  setEvidencePayload(null);
                }}
              />
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-sentinel-muted p-6 text-center">
                <Layers size={28} className="text-sentinel-accent" />
                <div className="text-sm font-semibold text-sentinel-text">
                  {evidenceFocusId ? 'Loading evidence chain…' : 'Right-click a node in Investigation → "Evidence chain"'}
                </div>
                <div className="text-xs max-w-sm font-mono">
                  Returns a column-DAG of SatellitePass / FMVClip / Document / Report / FeedEvent / Observation evidence for the selected focus node, with PostGIS provenance on each leaf.
                </div>
                <button type="button" onClick={() => setMode('investigation')} className="sentinel-btn">
                  ← Back to Investigation
                </button>
              </div>
            )
          ) : mode === 'ontology' ? (
            <OntologyOrbit onBack={() => setMode('investigation')} />
          ) : (
            <ForceGraph2D
              width={dimensions.width}
              height={dimensions.height}
              ref={graphRef}
              graphData={graphData}
              nodeId="id"
              nodeLabel={nodeTitle}
              linkLabel={(link: any) => { const s = linkScore(link); return `${link.type}${s !== undefined ? ` ${s.toFixed(2)}` : ''}`; }}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              linkColor={(link: any) => {
                const hot = selectedNode && (nodeId(link.source) === selectedNode.id || nodeId(link.target) === selectedNode.id);
                if (hot) return '#ff7a1a';
                if (link.candidate) return '#f5b400';
                // UX-AUDIT F22: edges tinted by predicate so the semantic
                // is legible at a glance, not just "lots of grey lines".
                return predicateColor(predicateOf(link));
              }}
              linkLineDash={(link: any) => link.candidate ? [4, 4] : link.__gnn ? [2, 3] : null}
              linkCanvasObjectMode={() => 'after'}
              linkCanvasObject={(link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
                // Only label edges once zoomed in enough to be readable.
                if (globalScale < 1.4) return;
                const s = link.source;
                const t = link.target;
                if (typeof s !== 'object' || typeof t !== 'object' || s.x == null || t.x == null) return;
                const label = predicateText(predicateOf(link));
                const mx = (s.x + t.x) / 2;
                const my = (s.y + t.y) / 2;
                const fontSize = Math.max(8 / globalScale, 2.6);
                ctx.font = `${fontSize}px JetBrains Mono, monospace`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                const w = ctx.measureText(label).width;
                ctx.fillStyle = 'rgba(10,13,16,0.78)';
                ctx.fillRect(mx - w / 2 - 1.5, my - fontSize / 2 - 0.5, w + 3, fontSize + 1);
                ctx.fillStyle = predicateColor(predicateOf(link));
                ctx.fillText(label, mx, my);
              }}
              linkWidth={(link: any) => {
                const hot = selectedNode && (nodeId(link.source) === selectedNode.id || nodeId(link.target) === selectedNode.id);
                return (hot ? 1.25 : 0.45) + linkWeight(link) * 1.1;
              }}
              backgroundColor="rgba(0,0,0,0)"
              onNodeClick={(node: any) => {
                if (pathPicker) completePath(node);
                else handleNodeClick(node);
              }}
              onNodeRightClick={handleNodeRightClick as any}
              onBackgroundClick={() => {
                if (pathPicker) cancelPath();
                setSelectedNode(null);
                setContextMenu(null);
              }}
              nodeCanvasObject={(node: any, ctx, globalScale) => {
                // Phase 5.E: cluster nodes get a distinct visual — larger
                // hollow ring with the member count in the centre. Clicking
                // expands.
                if (node?.__cluster) {
                  const count = Number(node?.properties?.count || 0);
                  const cls = String(node?.properties?.class || 'Node');
                  const color = kindColor(nodeKind({ label: cls, properties: {} }));
                  const radius = 8 + Math.min(6, Math.log2(count + 1));
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                  ctx.fillStyle = `${color}22`;
                  ctx.fill();
                  ctx.lineWidth = 1.4 / globalScale;
                  ctx.strokeStyle = color;
                  ctx.stroke();
                  const fontSize = Math.max(9 / globalScale, 3.0);
                  ctx.font = `bold ${fontSize}px JetBrains Mono, monospace`;
                  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                  ctx.fillStyle = color;
                  ctx.fillText(String(count), node.x, node.y);
                  ctx.font = `${fontSize * 0.85}px JetBrains Mono, monospace`;
                  ctx.fillStyle = '#aab2bb';
                  ctx.fillText(`${cls} cluster`, node.x, node.y + radius + fontSize);
                  node.__bckgDimensions = [radius * 2 + 4, radius * 2 + fontSize * 2];
                  return;
                }
                const label = nodeTitle(node);
                const kind = nodeKind(node);
                const color = kindColor(kind);
                const isSelected = selectedNode?.id === node.id;
                const isNeighbor = selectedNeighborIds.has(node.id);
                // A: scale the node by its PageRank centrality so hubs/brokers
                // stand out from leaf nodes (up to +3.5px over the base radius).
                const cent = centrality.map.get(node.id);
                const centBoost = cent && centrality.maxPagerank > 0
                  ? Math.min(3.5, (cent.pagerank / centrality.maxPagerank) * 3.5)
                  : 0;
                const radius = (isSelected ? 5.2 : 3.8) + centBoost;
                if (isSelected) {
                  ctx.beginPath();
                  ctx.setLineDash([2 / globalScale, 3 / globalScale]);
                  ctx.arc(node.x, node.y, radius + 8, 0, 2 * Math.PI, false);
                  ctx.strokeStyle = color;
                  ctx.lineWidth = 1 / globalScale;
                  ctx.stroke();
                  ctx.setLineDash([]);
                }
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + (isSelected ? 4 : 2), 0, 2 * Math.PI, false);
                ctx.fillStyle = isSelected ? `${color}33` : '#0b0d0f';
                ctx.fill();
                ctx.lineWidth = isSelected ? 1.4 / globalScale : isNeighbor ? 1 / globalScale : 0.6 / globalScale;
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
                ctx.fillStyle = isSelected ? '#e8ebee' : isNeighbor ? '#d7dde3' : '#aab2bb';
                ctx.fillText(label.slice(0, 28), node.x, node.y - radius - fontSize);
                node.__bckgDimensions = [Math.max(ctx.measureText(label).width, 14), fontSize + radius * 2];
              }}
              nodePointerAreaPaint={(node: any, color, ctx) => {
                ctx.fillStyle = color;
                const dims = node.__bckgDimensions || [50, 18];
                ctx.fillRect(node.x - dims[0] / 2, node.y - dims[1], dims[0], dims[1] + 10);
              }}
            />
          )}
          {mode === 'investigation' && (
            <>
              <div className="absolute left-3 top-3 w-52 border border-sentinel-line bg-sentinel-panel/90 p-3 font-mono text-[10px] text-sentinel-muted">
                <div className="sentinel-label mb-2 flex items-center gap-1"><Gauge size={11} /> Graph metrics</div>
                <div>NODES <span className="text-sentinel-accent">{graphData.nodes.length}</span></div>
                <div>EDGES <span className="text-sentinel-accent">{graphData.links.length}</span></div>
                <div>DENSITY <span className="text-sentinel-accent">{density}</span></div>
                {metrics && (
                  <>
                    <div>COMPONENTS <span className="text-sentinel-accent">{metrics.component_count}</span></div>
                    <div>LARGEST <span className="text-sentinel-accent">{metrics.largest_component}</span></div>
                    {metrics.top_centrality?.pagerank?.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-sentinel-line">
                        <div className="sentinel-label mb-1">Top central</div>
                        {metrics.top_centrality.pagerank.slice(0, 5).map((e: any) => (
                          <button
                            key={e.id}
                            type="button"
                            onClick={(event) => { event.stopPropagation(); selectNodeById(e.id); }}
                            className="w-full text-left truncate hover:text-sentinel-accent"
                            title={`${e.label || 'Node'} · PageRank ${Number(e.score).toFixed(4)}`}
                          >
                            <span className="text-sentinel-accent">{Number(e.score).toFixed(3)}</span> {e.name || e.label || String(e.id).slice(0, 10)}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="mt-1 text-[9px] text-sentinel-muted/70">via {metrics.backend}</div>
                  </>
                )}
              </div>
              <div className="absolute right-3 bottom-3 border border-sentinel-line bg-sentinel-panel/90 p-3 text-[10px] text-sentinel-muted">
                <div className="sentinel-label mb-2">Legend</div>
                {['facility', 'person', 'vehicle', 'vessel', 'phone', 'account'].map((kind) => (
                  <div key={kind} className="flex items-center gap-2 h-5">
                    <span className="w-2 h-2 inline-block" style={{ background: kindColor(kind) }} />
                    <span className="font-mono uppercase">{kind}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="graph-updates-strip border-t border-sentinel-line bg-sentinel-panel overflow-hidden">
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

      <aside className="graph-detail-panel sentinel-panel border-y-0 border-r-0 min-h-0 flex flex-col">
        <div className="sentinel-panel-header">
          <Info size={14} className="text-sentinel-accent" />
          <span>{pathResult ? 'Path' : siteRollup ? 'Site rollup' : gnnResult ? 'Suggested links' : 'Entity'}</span>
          {(pathResult || siteRollup || gnnResult) && (
            <button
              type="button"
              onClick={() => { setPathResult(null); setSiteRollup(null); if (gnnResult) clearGnnOverlay(); else setFilteredData(null); }}
              className="sentinel-icon-btn ml-auto h-6 w-6"
              title="Close"
            >
              <X size={13} />
            </button>
          )}
        </div>

        {pathResult ? (
          <div className="sentinel-scroll flex-1 p-3">
            <div className="sentinel-label mb-2">Paths from</div>
            <div className="text-xs mb-1 truncate">{nodeTitle(pathResult.from)}</div>
            <div className="sentinel-label mb-2">to</div>
            <div className="text-xs mb-3 truncate">{nodeTitle(pathResult.to)}</div>
            <div className="text-[10px] text-sentinel-muted font-mono mb-3">
              {pathResult.count ?? 0} shortest path(s); max depth {pathResult.max_depth ?? 4}
            </div>
            {(pathResult.paths || []).map((p: any, i: number) => (
              <div key={i} className="border border-sentinel-line bg-sentinel-bg p-2 mb-2">
                <div className="text-[10px] text-sentinel-muted font-mono mb-1">length {p.length}</div>
                <div className="flex flex-wrap items-center gap-x-1 gap-y-1 text-xs">
                  {p.nodes.map((n: any, j: number) => (
                    <span key={n.id}>
                      <span className="text-sentinel-text">{nodeTitle(n)}</span>
                      {j < (p.links || []).length && (
                        <span className="text-[9px] text-sentinel-muted mx-1 uppercase">
                          —{predicateText(predicateOf(p.links[j]))}→
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            ))}
            {(!pathResult.paths || pathResult.paths.length === 0) && (
              <div className="text-xs text-sentinel-muted font-mono">No path within depth.</div>
            )}
          </div>
        ) : siteRollup ? (
          <div className="sentinel-scroll flex-1 p-3 space-y-3">
            <div>
              <div className="sentinel-label mb-1">Site</div>
              <div className="text-sm font-semibold">{siteRollup.properties?.name || siteRollup.base_id}</div>
              <div className="text-[10px] text-sentinel-muted font-mono">
                radius {siteRollup.radius_m}m · last {siteRollup.recent_days}d
              </div>
            </div>
            <section className="border border-sentinel-line bg-sentinel-bg p-2">
              <div className="sentinel-label mb-2">Recent detections (by class)</div>
              {siteRollup.recent_detections?.length ? (
                <div className="space-y-1">
                  {siteRollup.recent_detections.map((row: any) => (
                    <div key={row.class} className="flex items-center text-xs">
                      <span className="truncate flex-1">{row.class}</span>
                      <span className="font-mono text-sentinel-accent">{row.count}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs text-sentinel-muted font-mono">none in window</div>}
            </section>
            {[
              ['vessels', 'Vessels'],
              ['vehicles', 'Vehicles'],
              ['aircraft', 'Aircraft'],
              ['other_assets', 'Other assets'],
            ].map(([key, label]) => (
              <section key={key} className="border border-sentinel-line bg-sentinel-bg p-2">
                <div className="sentinel-label mb-1">{label}</div>
                {(siteRollup[key as string] || []).length ? (
                  <div className="space-y-1">
                    {siteRollup[key as string].map((n: any) => (
                      <div key={n.id} className="text-xs truncate">
                        {n.properties?.name || n.id}
                      </div>
                    ))}
                  </div>
                ) : <div className="text-xs text-sentinel-muted font-mono">none</div>}
              </section>
            ))}
            <section className="border border-sentinel-line bg-sentinel-bg p-2">
              <div className="sentinel-label mb-1">FMV clips</div>
              {(siteRollup.fmv_clips || []).length ? (
                <div className="space-y-1">
                  {siteRollup.fmv_clips.map((clip: any) => (
                    <div key={clip.id} className="flex items-center gap-2 text-xs">
                      <span className="truncate flex-1">{clip.name || `clip-${clip.id}`}</span>
                      <span className="font-mono text-[10px] text-sentinel-muted">
                        {clip.overlapping_frames ?? 0}f · {clip.status || '—'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs text-sentinel-muted font-mono">none intersecting site</div>}
            </section>
            <section className="border border-sentinel-line bg-sentinel-bg p-2">
              <div className="sentinel-label mb-1">Reports</div>
              {(siteRollup.reports || []).length ? (
                <div className="space-y-1">
                  {siteRollup.reports.map((report: any) => (
                    <div key={report.id} className="flex items-center gap-2 text-xs">
                      <span className="truncate flex-1">{report.title || `report-${report.id}`}</span>
                      <span className="font-mono text-[10px] text-sentinel-muted">{report.report_type || report.status || '—'}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs text-sentinel-muted font-mono">none linked to site entities</div>}
            </section>
          </div>
        ) : gnnResult ? (
          <div className="sentinel-scroll flex-1 p-3 space-y-3">
            <div>
              <div className="sentinel-label mb-1 flex items-center gap-1"><Sparkles size={12} /> GNN link prediction</div>
              <div className="text-[10px] text-sentinel-muted font-mono">
                GraphSAGE · {gnnResult.node_count ?? 0} nodes · {gnnResult.candidate_count ?? 0} candidate pairs
              </div>
            </div>
            {gnnResult.error ? (
              <div className="text-xs text-sentinel-warning font-mono border border-sentinel-line bg-sentinel-bg p-2">{gnnResult.error}</div>
            ) : (gnnResult.suggestions || []).length ? (
              <div className="space-y-1">
                {gnnResult.suggestions.map((s: any, i: number) => (
                  <button
                    key={`${s.source}-${s.target}-${i}`}
                    type="button"
                    onClick={() => selectNodeById(s.source)}
                    className="w-full text-left border border-sentinel-line bg-sentinel-bg p-2 hover:border-sentinel-accent"
                    title="Locate the source entity"
                  >
                    <div className="flex items-center gap-2 text-xs">
                      <span className="truncate flex-1">{s.source_name || s.source_label || String(s.source).slice(0, 10)}</span>
                      <span className="text-sentinel-muted">↔</span>
                      <span className="truncate flex-1 text-right">{s.target_name || s.target_label || String(s.target).slice(0, 10)}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="h-1.5 flex-1 border border-sentinel-line bg-sentinel-panel">
                        <span className="block h-full bg-sentinel-accent" style={{ width: `${Math.round((Number(s.score) || 0) * 100)}%` }} />
                      </span>
                      <span className="font-mono text-[10px] text-sentinel-accent">{(Number(s.score) || 0).toFixed(3)}</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-xs text-sentinel-muted font-mono">No suggestions returned.</div>
            )}
            <div className="text-[10px] text-sentinel-muted/70 font-mono">
              Advisory only — predictions persist as GNN_SUGGESTED_LINK edges for review, not as approved relationships.
            </div>
          </div>
        ) : selectedNode ? (
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
                  const weight = linkWeight(link);
                  return (
                    <div key={`${otherId}-${index}`} className="grid grid-cols-[22px_minmax(0,1fr)_48px] gap-2 items-center py-1.5">
                      <NodeGlyph kind={nodeKind(other)} size={13} />
                      <span className="truncate text-xs">{other ? nodeTitle(other) : otherId}</span>
                      <span className="h-2 border border-sentinel-line bg-sentinel-bg">
                        <span className="block h-full bg-sentinel-accent" style={{ width: `${weight * 100}%` }} />
                      </span>
                    </div>
                  );
                }) : <div className="text-xs text-sentinel-muted font-mono">No visible adjacent links.</div>}
              </section>
              <section className="p-3 border-b border-sentinel-line">
                <div className="sentinel-label mb-2 flex items-center">
                  <span>Co-occurrence over window</span>
                  <span className="ml-auto font-mono text-[10px] text-sentinel-muted">{cooccurrenceBars.total} linked</span>
                </div>
                {cooccurrenceBars.total > 0 ? (
                  (() => {
                    const max = Math.max(1, ...cooccurrenceBars.counts);
                    return (
                      <div className="flex h-14 items-end gap-1 border border-sentinel-line bg-sentinel-bg p-2">
                        {cooccurrenceBars.counts.map((count, index) => {
                          const height = (count / max) * 100;
                          return (
                            <span
                              key={index}
                              title={`${count} linked entit${count === 1 ? 'y' : 'ies'}`}
                              className="flex-1 bg-sentinel-accent"
                              style={{ height: `${Math.max(count > 0 ? 6 : 0, height)}%`, opacity: 0.45 + height / 180 }}
                            />
                          );
                        })}
                      </div>
                    );
                  })()
                ) : (
                  <div className="text-xs text-sentinel-muted font-mono">No time-stamped links in window.</div>
                )}
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
          className="fixed z-50 w-56 border border-sentinel-line-2 bg-sentinel-panel shadow-2xl py-1 text-xs"
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
          <button type="button" onClick={() => startPathPick(contextMenu.node)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Route size={14} /> Find path to…
          </button>
          <button type="button" onClick={() => openEvidenceChain(contextMenu.node)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Layers size={14} /> Evidence chain
          </button>
          {selectedIsSite && (
            <button type="button" onClick={() => rollupSite(contextMenu.node)} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
              <Layers size={14} /> Roll up to site
            </button>
          )}
          <button type="button" onClick={exportSelected} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 flex items-center gap-2">
            <Share2 size={14} /> Export Selection
          </button>
          {filteredData && (
            <button type="button" onClick={() => { setFilteredData(null); setPathResult(null); setSiteRollup(null); }} className="w-full text-left px-3 py-2 hover:bg-sentinel-panel-2 text-sentinel-muted">
              Clear Filter
            </button>
          )}
        </div>
      )}
    </div>
  );
}
