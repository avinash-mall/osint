import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import axios from 'axios';
import { AlertCircle, ChevronRight, GitBranch, HelpCircle, Layers, X } from 'lucide-react';
import { assignUnknownLabel } from '../../utils/ontologyApi';

const API_URL = import.meta.env.VITE_API_URL || '';

interface OntologyNode {
  id: string;
  label: string;
  labels?: string[];
  properties?: Record<string, any>;
}

interface OntologyLink {
  source: string;
  target: string;
  type?: string;
  predicate?: string;
  properties?: Record<string, any>;
}

interface OntologyPayload {
  nodes: OntologyNode[];
  links: OntologyLink[];
  include_unknown: boolean;
  supports_per_unknown: number;
}

interface Branch { id: string; label: string }
interface ObjectRow { id: string; branch_id: string; label: string }

function nodeColor(node: OntologyNode): string {
  const labels = new Set<string>([node.label, ...(node.labels || [])]);
  if (labels.has('UnknownLabel')) return '#ff7a1a';
  if (labels.has('OntologyBranch')) return '#5fc4ff';
  if (labels.has('OntologyObject')) return '#3dd68c';
  if (labels.has('Detection')) return '#f5b400';
  return '#9bb1c4';
}

function nodeTitle(node: OntologyNode): string {
  const p = node.properties || {};
  return String(p.label || p.name || p.id || node.label);
}

interface Props {
  onBack?: () => void;
}

export function OntologyOrbit({ onBack }: Props) {
  const [payload, setPayload] = useState<OntologyPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<OntologyNode | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [objectsByBranch, setObjectsByBranch] = useState<Record<string, ObjectRow[]>>({});
  const [assignMode, setAssignMode] = useState<'existing' | 'create'>('existing');
  const [pickedBranch, setPickedBranch] = useState<string>('');
  const [pickedObject, setPickedObject] = useState<string>('');
  const [newObjectLabel, setNewObjectLabel] = useState<string>('');
  const [newObjectPrompt, setNewObjectPrompt] = useState<string>('');
  const [assignBusy, setAssignBusy] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 900, height: 600 });
  const paneRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const resp = await axios.get(`${API_URL}/api/graph/ontology`, {
        params: { include_unknown: true, supports_per_unknown: 5 },
      });
      setPayload(resp.data);
    } catch (err: any) {
      console.error('ontology fetch failed', err);
      setError(err?.message || 'failed to load');
      setPayload({ nodes: [], links: [], include_unknown: false, supports_per_unknown: 0 });
    }
  }, []);

  useEffect(() => { load().catch(() => {}); }, [load]);

  useEffect(() => {
    // Load ontology tree once for the assignment popover.
    (async () => {
      try {
        const resp = await axios.get(`${API_URL}/api/ontology`);
        const tree = resp.data;
        if (tree?.branches && Array.isArray(tree.branches)) {
          setBranches(tree.branches.map((b: any) => ({ id: b.id, label: b.label })));
        }
        if (tree?.objects && Array.isArray(tree.objects)) {
          const grouped: Record<string, ObjectRow[]> = {};
          for (const o of tree.objects) {
            (grouped[o.branch_id] ||= []).push({ id: o.id, branch_id: o.branch_id, label: o.label });
          }
          setObjectsByBranch(grouped);
        }
      } catch (e) {
        // Non-fatal — the orbit still renders.
        console.warn('ontology tree fetch failed', e);
      }
    })();
  }, []);

  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      if (!entries[0]) return;
      setDimensions({
        width: Math.max(320, entries[0].contentRect.width),
        height: Math.max(320, entries[0].contentRect.height),
      });
    });
    if (paneRef.current) observer.observe(paneRef.current);
    return () => observer.disconnect();
  }, []);

  const graphData = useMemo(() => payload || { nodes: [], links: [] }, [payload]);

  const selectedIsUnknown = useMemo(() => {
    if (!selected) return false;
    const labels = new Set<string>([selected.label, ...(selected.labels || [])]);
    return labels.has('UnknownLabel');
  }, [selected]);

  const unknownLabelText = useMemo(() => {
    if (!selectedIsUnknown || !selected) return '';
    return String(selected.properties?.label || selected.label || '');
  }, [selectedIsUnknown, selected]);

  const submitAssignment = useCallback(async () => {
    if (!unknownLabelText || !pickedBranch) return;
    setAssignBusy(true);
    try {
      if (assignMode === 'existing') {
        if (!pickedObject) throw new Error('pick an object');
        await assignUnknownLabel(unknownLabelText, { branch_id: pickedBranch, object_id: pickedObject });
      } else {
        if (!newObjectLabel) throw new Error('label required');
        await assignUnknownLabel(unknownLabelText, {
          branch_id: pickedBranch,
          create_object: {
            label: newObjectLabel,
            prompt: newObjectPrompt || newObjectLabel,
          },
        });
      }
      // Refresh the orbit so the resolved label disappears.
      setSelected(null);
      setPickedBranch('');
      setPickedObject('');
      setNewObjectLabel('');
      setNewObjectPrompt('');
      await load();
    } catch (err: any) {
      console.error('assign failed', err);
      setError(err?.message || 'assignment failed');
    } finally {
      setAssignBusy(false);
    }
  }, [unknownLabelText, pickedBranch, pickedObject, assignMode, newObjectLabel, newObjectPrompt, load]);

  const objectOptions = useMemo(() => objectsByBranch[pickedBranch] || [], [objectsByBranch, pickedBranch]);

  const counts = useMemo(() => {
    if (!payload) return { branches: 0, objects: 0, unknowns: 0 };
    const c = { branches: 0, objects: 0, unknowns: 0 };
    for (const n of payload.nodes) {
      const labels = new Set<string>([n.label, ...(n.labels || [])]);
      if (labels.has('UnknownLabel')) c.unknowns += 1;
      else if (labels.has('OntologyBranch')) c.branches += 1;
      else if (labels.has('OntologyObject')) c.objects += 1;
    }
    return c;
  }, [payload]);

  return (
    <div className="ontology-orbit absolute inset-0 flex flex-col overflow-hidden bg-[#0a0d10]">
      <div className="sentinel-panel-header">
        <GitBranch size={14} className="text-sentinel-accent" />
        <span>Ontology · {counts.branches} branches · {counts.objects} objects · {counts.unknowns} unknown</span>
        <button type="button" onClick={load} className="ml-auto sentinel-btn">Refresh</button>
        {onBack && (
          <button type="button" onClick={onBack} className="sentinel-btn">← Investigation</button>
        )}
      </div>
      {error && (
        <div className="px-3 py-1.5 text-[11px] font-mono bg-sentinel-warning/20 text-sentinel-warning">{error}</div>
      )}
      <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,1fr)_320px] gap-2 p-2 overflow-hidden">
        <div ref={paneRef} className="relative border border-sentinel-line bg-sentinel-panel min-h-0 overflow-hidden">
          <ForceGraph2D
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeId="id"
            nodeLabel={(n: any) => nodeTitle(n)}
            linkLabel={(l: any) => String(l.type || l.predicate || '')}
            linkDirectionalArrowLength={3}
            linkDirectionalArrowRelPos={1}
            linkColor={(l: any) => {
              const t = String(l.predicate || l.type || '');
              if (t === 'HAS_OBJECT') return '#3dd68c';
              if (t === 'HAS_CHILD') return '#5fc4ff';
              if (t === 'SUGGESTED_BRANCH') return '#ff7a1a';
              if (t === 'LABEL_OF') return '#f5b400';
              return '#9bb1c4';
            }}
            linkWidth={0.8}
            backgroundColor="rgba(0,0,0,0)"
            onNodeClick={(n: any) => setSelected(n)}
            nodeCanvasObject={(node: any, ctx, scale) => {
              const color = nodeColor(node);
              const isSelected = selected?.id === node.id;
              const radius = isSelected ? 5.5 : 4;
              ctx.beginPath();
              ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
              ctx.fillStyle = color;
              ctx.fill();
              const fontSize = Math.max(9 / scale, 2.8);
              ctx.font = `${fontSize}px JetBrains Mono, monospace`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillStyle = isSelected ? '#e8ebee' : '#aab2bb';
              ctx.fillText(nodeTitle(node).slice(0, 26), node.x, node.y - radius - fontSize);
            }}
          />
          <div className="absolute right-3 bottom-3 border border-sentinel-line bg-sentinel-panel/90 p-3 text-[10px] text-sentinel-muted">
            <div className="sentinel-label mb-2">Legend</div>
            {[
              ['branch', '#5fc4ff'],
              ['object', '#3dd68c'],
              ['unknown', '#ff7a1a'],
              ['detection', '#f5b400'],
            ].map(([k, c]) => (
              <div key={k} className="flex items-center gap-2 h-5">
                <span className="w-2 h-2 inline-block" style={{ background: c }} />
                <span className="font-mono uppercase">{k}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="border border-sentinel-line bg-sentinel-panel p-3 overflow-y-auto">
          {selected ? (
            <>
              <div className="flex items-center gap-2 mb-2">
                {selectedIsUnknown ? <HelpCircle size={14} className="text-sentinel-accent" /> : <Layers size={14} className="text-sentinel-accent" />}
                <div className="sentinel-label">{selected.label}</div>
                <button type="button" onClick={() => setSelected(null)} className="ml-auto sentinel-icon-btn h-6 w-6">
                  <X size={13} />
                </button>
              </div>
              <div className="text-sm font-semibold mb-1 break-words">{nodeTitle(selected)}</div>
              <div className="text-[10px] text-sentinel-muted font-mono mb-3">{String(selected.id).slice(0, 24)}</div>
              {selectedIsUnknown ? (
                <div className="space-y-3">
                  <div className="text-xs text-sentinel-muted">
                    Triage: assign this label to an existing object or mint a new one.
                  </div>
                  <div className="flex border border-sentinel-line-2 h-6">
                    <button
                      type="button"
                      onClick={() => setAssignMode('existing')}
                      className={`flex-1 px-2 text-[10px] font-mono uppercase ${assignMode === 'existing' ? 'bg-sentinel-accent text-sentinel-bg' : 'text-sentinel-muted'}`}
                    >Existing</button>
                    <button
                      type="button"
                      onClick={() => setAssignMode('create')}
                      className={`flex-1 px-2 text-[10px] font-mono uppercase border-l border-sentinel-line-2 ${assignMode === 'create' ? 'bg-sentinel-accent text-sentinel-bg' : 'text-sentinel-muted'}`}
                    >Create</button>
                  </div>
                  <label className="block text-[10px] text-sentinel-muted font-mono">
                    Branch
                    <select
                      value={pickedBranch}
                      onChange={(e) => { setPickedBranch(e.target.value); setPickedObject(''); }}
                      className="w-full mt-1 bg-sentinel-bg border border-sentinel-line-2 text-xs text-sentinel-text p-1"
                    >
                      <option value="">— pick a branch —</option>
                      {branches.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
                    </select>
                  </label>
                  {assignMode === 'existing' ? (
                    <label className="block text-[10px] text-sentinel-muted font-mono">
                      Object
                      <select
                        value={pickedObject}
                        onChange={(e) => setPickedObject(e.target.value)}
                        disabled={!pickedBranch}
                        className="w-full mt-1 bg-sentinel-bg border border-sentinel-line-2 text-xs text-sentinel-text p-1 disabled:opacity-50"
                      >
                        <option value="">— pick an object —</option>
                        {objectOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </select>
                    </label>
                  ) : (
                    <>
                      <label className="block text-[10px] text-sentinel-muted font-mono">
                        New object label
                        <input
                          value={newObjectLabel}
                          onChange={(e) => setNewObjectLabel(e.target.value)}
                          className="w-full mt-1 bg-sentinel-bg border border-sentinel-line-2 text-xs text-sentinel-text p-1"
                        />
                      </label>
                      <label className="block text-[10px] text-sentinel-muted font-mono">
                        Prompt (defaults to label)
                        <input
                          value={newObjectPrompt}
                          onChange={(e) => setNewObjectPrompt(e.target.value)}
                          className="w-full mt-1 bg-sentinel-bg border border-sentinel-line-2 text-xs text-sentinel-text p-1"
                        />
                      </label>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={submitAssignment}
                    disabled={assignBusy || !pickedBranch || (assignMode === 'existing' ? !pickedObject : !newObjectLabel)}
                    className="sentinel-btn primary w-full justify-center disabled:opacity-50"
                  >
                    <ChevronRight size={13} /> {assignBusy ? 'Assigning…' : `Assign "${unknownLabelText.slice(0, 22)}"`}
                  </button>
                </div>
              ) : (
                <pre className="text-[10px] font-mono whitespace-pre-wrap break-words text-sentinel-text border border-sentinel-line bg-sentinel-bg p-2 max-h-60 overflow-y-auto">
                  {JSON.stringify(selected.properties || {}, null, 2)}
                </pre>
              )}
            </>
          ) : (
            <div className="text-xs text-sentinel-muted font-mono flex items-start gap-1">
              <AlertCircle size={13} />
              Click a node — UnknownLabel nodes open a triage assignment popover.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
