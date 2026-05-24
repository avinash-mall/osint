import { useMemo, useState } from 'react';
import { Activity, AlertOctagon, ChevronRight, File, FileText, MapPin, Radio, Satellite, Video } from 'lucide-react';

interface EvidenceNode {
  id: string;
  label: string;
  labels?: string[];
  properties?: Record<string, any>;
}

interface EvidenceLink {
  source: string;
  target: string;
  predicate?: string;
  type?: string;
  properties?: Record<string, any>;
}

export interface EvidencePayload {
  focus: EvidenceNode;
  nodes: EvidenceNode[];
  links: EvidenceLink[];
  evidence_records: {
    detections?: any[];
    satellite_passes?: any[];
    fmv_clips?: any[];
    fmv_frames?: any[];
    documents?: any[];
    reports?: any[];
    feed_events?: any[];
    observations?: any[];
    transcripts?: any[];
  };
}

interface Props {
  payload: EvidencePayload;
  onContradict?: (actorId: string, detectionPostgisId: number) => void;
  onClose?: () => void;
}

const COLUMNS: { key: string; label: string; icon: any; nodeLabels: string[]; bucketKey: keyof EvidencePayload['evidence_records'] }[] = [
  { key: 'satellite', label: 'Satellite passes', icon: Satellite, nodeLabels: ['SatellitePass'], bucketKey: 'satellite_passes' },
  { key: 'detections', label: 'Detections', icon: Activity, nodeLabels: ['Detection'], bucketKey: 'detections' },
  { key: 'fmv', label: 'FMV clips', icon: Video, nodeLabels: ['FMVClip', 'FMVDetection'], bucketKey: 'fmv_clips' },
  { key: 'documents', label: 'Documents', icon: FileText, nodeLabels: ['Document'], bucketKey: 'documents' },
  { key: 'reports', label: 'Reports', icon: File, nodeLabels: ['Report'], bucketKey: 'reports' },
  { key: 'feed', label: 'Feed events', icon: Radio, nodeLabels: ['FeedEvent'], bucketKey: 'feed_events' },
  { key: 'observations', label: 'Observations', icon: MapPin, nodeLabels: ['Observation'], bucketKey: 'observations' },
];

const TIER_COLORS: Record<string, string> = {
  confirmed: '#3dd68c',
  candidate: '#f5b400',
  discovery: '#9bb1c4',
};

function evidenceTier(node: EvidenceNode, postgisRow?: any): string | undefined {
  const review = node?.properties?.review_status || postgisRow?.metadata?.review_status;
  if (review === 'confirmed') return 'confirmed';
  if (review === 'candidate') return 'candidate';
  if (review === 'discovery') return 'discovery';
  return undefined;
}

function describe(node: EvidenceNode): string {
  const p = node.properties || {};
  return String(p.name || p.title || p.class || p.id || node.id);
}

export function EvidenceColumnDAG({ payload, onContradict, onClose }: Props) {
  const [selectedLeaf, setSelectedLeaf] = useState<{ node: EvidenceNode; row?: any } | null>(null);

  // Group focus + nodes by column.
  const byColumn = useMemo(() => {
    const map: Record<string, EvidenceNode[]> = {};
    for (const col of COLUMNS) map[col.key] = [];
    for (const node of payload.nodes) {
      if (node.id === payload.focus.id) continue;
      const labels = new Set<string>([node.label, ...(node.labels || [])]);
      for (const col of COLUMNS) {
        if (col.nodeLabels.some((l) => labels.has(l))) {
          map[col.key].push(node);
          break;
        }
      }
    }
    return map;
  }, [payload]);

  // PostGIS records indexed by postgis_id for quick lookup.
  const recordIndex = useMemo(() => {
    const idx: Record<string, Record<number, any>> = {};
    for (const col of COLUMNS) {
      idx[col.key] = {};
      const rows = (payload.evidence_records as any)[col.bucketKey] || [];
      for (const row of rows) {
        if (typeof row?.id === 'number') idx[col.key][row.id] = row;
      }
    }
    return idx;
  }, [payload]);

  const totalLeaves = useMemo(() => {
    return Object.values(byColumn).reduce((sum, arr) => sum + arr.length, 0);
  }, [byColumn]);

  return (
    <div className="evidence-dag absolute inset-0 flex flex-col overflow-hidden bg-[#0a0d10] text-sentinel-text">
      <div className="sentinel-panel-header">
        <Activity size={14} className="text-sentinel-accent" />
        <span>Evidence chain · {describe(payload.focus)}</span>
        <span className="ml-2 text-[10px] text-sentinel-muted font-mono">
          {totalLeaves} leaf{totalLeaves === 1 ? '' : 's'}
        </span>
        {onClose && (
          <button type="button" onClick={onClose} className="ml-auto sentinel-btn">
            Close
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 grid grid-cols-[200px_minmax(0,1fr)_300px] gap-2 p-2 overflow-hidden">
        {/* Left: focus card */}
        <div className="border border-sentinel-line bg-sentinel-panel p-3 overflow-y-auto">
          <div className="sentinel-label mb-2">Focus</div>
          <div className="text-sm font-semibold">{describe(payload.focus)}</div>
          <div className="text-[10px] text-sentinel-muted font-mono mt-1">
            {payload.focus.label}
          </div>
          <div className="mt-3 space-y-1">
            {Object.entries(payload.focus.properties || {}).slice(0, 12).map(([k, v]) => (
              <div key={k} className="text-[10px] font-mono">
                <span className="text-sentinel-muted">{k}: </span>
                <span className="break-words">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Center: columns */}
        <div className="grid grid-flow-col auto-cols-[minmax(160px,1fr)] gap-2 overflow-x-auto">
          {COLUMNS.map((col) => {
            const Icon = col.icon;
            const nodes = byColumn[col.key] || [];
            return (
              <div key={col.key} className="border border-sentinel-line bg-sentinel-panel min-w-0 flex flex-col">
                <div className="sentinel-panel-header">
                  <Icon size={13} className="text-sentinel-accent" />
                  <span className="truncate">{col.label}</span>
                  <span className="ml-auto sentinel-tag">{nodes.length}</span>
                </div>
                <div className="sentinel-scroll p-2 space-y-1.5">
                  {nodes.length === 0 ? (
                    <div className="text-[10px] text-sentinel-muted font-mono">empty</div>
                  ) : nodes.map((node) => {
                    const row = recordIndex[col.key][node.properties?.postgis_id];
                    const tier = evidenceTier(node, row);
                    const isSelected = selectedLeaf?.node.id === node.id;
                    return (
                      <button
                        type="button"
                        key={node.id}
                        onClick={() => setSelectedLeaf({ node, row })}
                        className={`w-full text-left border bg-sentinel-bg p-2 hover:border-sentinel-accent transition-colors ${isSelected ? 'border-sentinel-accent' : 'border-sentinel-line'}`}
                      >
                        <div className="flex items-center gap-2">
                          {tier && (
                            <span
                              className="w-2 h-2 inline-block rounded-full"
                              style={{ background: TIER_COLORS[tier] }}
                              title={tier}
                            />
                          )}
                          <span className="text-xs truncate">{describe(node)}</span>
                        </div>
                        {row && (
                          <div className="mt-1 text-[10px] text-sentinel-muted font-mono">
                            {row.confidence !== undefined && (
                              <span>conf {Number(row.confidence).toFixed(2)} · </span>
                            )}
                            {row.class && <span>{row.class}</span>}
                            {row.media_type && <span>{row.media_type}</span>}
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>

        {/* Right: provenance popover */}
        <div className="border border-sentinel-line bg-sentinel-panel p-3 overflow-y-auto">
          <div className="sentinel-label mb-2">Provenance</div>
          {selectedLeaf ? (
            <>
              <div className="text-sm font-semibold mb-1">{describe(selectedLeaf.node)}</div>
              <div className="text-[10px] text-sentinel-muted font-mono mb-2">
                {selectedLeaf.node.label} · {String(selectedLeaf.node.id).slice(0, 12)}
              </div>
              {(selectedLeaf.node.label === 'Detection' || selectedLeaf.node.label === 'OntologyCandidate') && onContradict && selectedLeaf.row?.id && (
                <button
                  type="button"
                  className="sentinel-btn warn w-full mb-3 justify-center"
                  onClick={() => onContradict(selectedLeaf.node.id, selectedLeaf.row.id)}
                  title="Mark this detection as contradicting the focus"
                >
                  <AlertOctagon size={13} /> Contradict
                </button>
              )}
              {selectedLeaf.row ? (
                <>
                  <div className="sentinel-label mb-1">PostGIS row</div>
                  <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-sentinel-text border border-sentinel-line bg-sentinel-bg p-2 max-h-60 overflow-y-auto">
                    {JSON.stringify(selectedLeaf.row, null, 2)}
                  </pre>
                </>
              ) : (
                <div className="text-xs text-sentinel-muted font-mono">No PostGIS record (graph stub only).</div>
              )}
              {Object.keys(selectedLeaf.node.properties || {}).length > 0 && (
                <>
                  <div className="sentinel-label mt-3 mb-1">Graph properties</div>
                  <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-sentinel-text border border-sentinel-line bg-sentinel-bg p-2 max-h-40 overflow-y-auto">
                    {JSON.stringify(selectedLeaf.node.properties, null, 2)}
                  </pre>
                </>
              )}
            </>
          ) : (
            <div className="text-xs text-sentinel-muted font-mono flex items-start gap-1">
              <ChevronRight size={13} />
              Select an evidence leaf to see its provenance.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
