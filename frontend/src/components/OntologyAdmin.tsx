/**
 * Single-tenant admin UI for editing the live ontology.
 *
 * Mounted in App.tsx via the `admin` workspace tab. No auth — by design.
 *
 * The right pane reflects the current selection (`mode` is one of
 * 'branch' | 'object' | 'new-branch' | 'new-object' | null). Mutations go
 * through `utils/ontologyApi.ts`; on success we call `useOntology().refresh()`
 * so the tree (and every other consumer in the app) reloads immediately
 * without waiting for the 30s version watcher.
 */
import axios from 'axios';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import {
  flattenBranches,
  flattenObjects,
  useOntology,
  type OntologyBranch,
  type OntologyObject,
} from '../utils/useOntology';
import { ICON_LIBRARY, IconRenderer, type IconCategory, type IconEntry } from '../utils/iconLibrary';
import {
  assignUnknownLabel,
  createBranch,
  createObject,
  deleteBranch,
  deleteObject,
  listUnknownLabels,
  updateBranch,
  updateObject,
  type BranchPayload,
  type ObjectPayload,
  type UnknownLabel,
} from '../utils/ontologyApi';

const KNOWN_SENSORS = ['optical', 'sar', 'thermal', 'multispectral', 'hyperspectral'];

type EditorMode =
  | { kind: 'none' }
  | { kind: 'branch'; id: string }
  | { kind: 'object'; id: string }
  | { kind: 'new-branch'; parentId: string | null }
  | { kind: 'new-object'; branchId: string };

interface BranchFormState {
  id: string;
  parent_id: string | null;
  label: string;
  color: string;
  short: string;
  icon_key: string;
  matchersText: string;
  sensors: string[];
  order_index: number;
}

interface ObjectFormState {
  id: string;
  branch_id: string;
  label: string;
  prompt: string;
  sensors: string[];
  min_gsd_meters: string;
  icon_key: string;
  order_index: number;
}

function emptyBranch(parentId: string | null): BranchFormState {
  return {
    id: '',
    parent_id: parentId,
    label: '',
    color: '#888888',
    short: '',
    icon_key: '',
    matchersText: '',
    sensors: ['optical'],
    order_index: 0,
  };
}

function emptyObject(branchId: string): ObjectFormState {
  return {
    id: '',
    branch_id: branchId,
    label: '',
    prompt: '',
    sensors: ['optical'],
    min_gsd_meters: '',
    icon_key: '',
    order_index: 0,
  };
}

function branchToForm(b: OntologyBranch): BranchFormState {
  return {
    id: b.id,
    parent_id: b.parent_id ?? null,
    label: b.label || '',
    color: b.color || '#888888',
    short: b.short || '',
    icon_key: b.icon_key || '',
    matchersText: (b.matchers || []).join('\n'),
    sensors: b.sensors || [],
    order_index: b.order_index ?? 0,
  };
}

function objectToForm(o: OntologyObject): ObjectFormState {
  return {
    id: o.id,
    branch_id: o.branch_id,
    label: o.label || '',
    prompt: o.prompt || '',
    sensors: o.sensors || [],
    min_gsd_meters: o.min_gsd_meters == null ? '' : String(o.min_gsd_meters),
    icon_key: o.icon_key || '',
    order_index: o.order_index ?? 0,
  };
}

// ---------------------------------------------------------------------------
// Icon picker
// ---------------------------------------------------------------------------

interface IconPickerProps {
  value: string;
  onChange: (key: string) => void;
}

function IconPicker({ value, onChange }: IconPickerProps) {
  const [query, setQuery] = useState('');
  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = new Map<IconCategory, IconEntry[]>();
    for (const entry of ICON_LIBRARY) {
      if (q) {
        const haystack = [entry.key, ...(entry.keywords || [])].join(' ').toLowerCase();
        if (!haystack.includes(q)) continue;
      }
      const arr = out.get(entry.category) || [];
      arr.push(entry);
      out.set(entry.category, arr);
    }
    return Array.from(out.entries());
  }, [query]);

  return (
    <div className="border border-slate-800 rounded p-2 bg-slate-950/40">
      <div className="flex items-center gap-2 mb-2">
        <Search className="w-3.5 h-3.5 text-slate-500" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search icons by keyword…"
          className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs"
        />
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            className="font-mono text-[10px] uppercase tracking-wider text-slate-400 border border-slate-700 px-2 py-1 rounded hover:text-slate-100"
          >
            Clear
          </button>
        )}
      </div>
      <div className="max-h-48 overflow-y-auto pr-1 space-y-2">
        {grouped.length === 0 && (
          <div className="font-mono text-[10px] text-slate-500 italic">No icons match.</div>
        )}
        {grouped.map(([category, entries]) => (
          <div key={category}>
            <div className="font-mono text-[9px] uppercase tracking-wider text-slate-500 mb-1">
              {category}
            </div>
            <div className="flex flex-wrap gap-1">
              {entries.map((entry) => {
                const Comp = entry.Component;
                const selected = entry.key === value;
                return (
                  <button
                    key={entry.key}
                    type="button"
                    title={`${entry.key} — ${entry.keywords.join(', ')}`}
                    onClick={() => onChange(entry.key)}
                    className={`grid place-items-center w-7 h-7 rounded border transition ${
                      selected
                        ? 'border-blue-400 bg-blue-500/20 text-blue-200'
                        : 'border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500'
                    }`}
                  >
                    <Comp className="w-3.5 h-3.5" />
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {value && (
        <div className="mt-2 font-mono text-[10px] text-slate-400 flex items-center gap-2">
          <span className="text-slate-500">selected:</span>
          <IconRenderer iconKey={value} className="w-3.5 h-3.5" />
          <span>{value}</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sensors multi-select (a row of toggleable pills)
// ---------------------------------------------------------------------------

interface SensorPickerProps {
  value: string[];
  onChange: (next: string[]) => void;
}

function SensorPicker({ value, onChange }: SensorPickerProps) {
  const set = new Set(value);
  return (
    <div className="flex flex-wrap gap-1">
      {KNOWN_SENSORS.map((s) => {
        const on = set.has(s);
        return (
          <button
            key={s}
            type="button"
            onClick={() => {
              const next = new Set(set);
              if (on) next.delete(s);
              else next.add(s);
              onChange(Array.from(next));
            }}
            className={`font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded border transition ${
              on
                ? 'border-blue-400 bg-blue-500/20 text-blue-200'
                : 'border-slate-700 bg-slate-900 text-slate-400 hover:text-slate-100'
            }`}
          >
            {s}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Branch form
// ---------------------------------------------------------------------------

interface BranchFormProps {
  state: BranchFormState;
  isNew: boolean;
  branches: OntologyBranch[];
  busy: boolean;
  onChange: (next: BranchFormState) => void;
  onSave: () => void;
  onDelete?: (force: boolean) => void;
  onCancel: () => void;
  error: string | null;
  conflict: { type: 'detections'; affected: number } | null;
}

function BranchForm({
  state,
  isNew,
  branches,
  busy,
  onChange,
  onSave,
  onDelete,
  onCancel,
  error,
  conflict,
}: BranchFormProps) {
  const [forceDelete, setForceDelete] = useState(false);
  // Reset force flag whenever the conflict clears or the selection changes.
  useEffect(() => {
    if (!conflict) setForceDelete(false);
  }, [conflict, state.id]);

  const parentOptions = useMemo(() => {
    // Don't allow choosing self as parent.
    return flattenBranches(branches).filter((b) => b.id !== state.id);
  }, [branches, state.id]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-100">
          {isNew ? 'New branch' : 'Edit branch'}
        </h3>
        <span className="font-mono text-[10px] text-slate-500">
          {isNew ? 'create' : `id: ${state.id}`}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">id</span>
          <input
            type="text"
            value={state.id}
            disabled={!isNew}
            onChange={(e) => onChange({ ...state, id: e.target.value })}
            placeholder="e.g. Armor"
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">label</span>
          <input
            type="text"
            value={state.label}
            onChange={(e) => onChange({ ...state, label: e.target.value })}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
          />
        </label>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">color</span>
          <div className="flex items-center gap-1">
            <input
              type="color"
              value={state.color || '#888888'}
              onChange={(e) => onChange({ ...state, color: e.target.value })}
              className="h-8 w-10 bg-slate-900 border border-slate-700 rounded cursor-pointer"
            />
            <input
              type="text"
              value={state.color}
              onChange={(e) => onChange({ ...state, color: e.target.value })}
              className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
            />
          </div>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">short</span>
          <input
            type="text"
            value={state.short}
            maxLength={6}
            onChange={(e) => onChange({ ...state, short: e.target.value })}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono uppercase"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">order</span>
          <input
            type="number"
            value={state.order_index}
            onChange={(e) => onChange({ ...state, order_index: Number(e.target.value || 0) })}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">parent</span>
        <select
          value={state.parent_id || ''}
          onChange={(e) => onChange({ ...state, parent_id: e.target.value || null })}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
        >
          <option value="">(root)</option>
          {parentOptions.map((b) => (
            <option key={b.id} value={b.id}>
              {b.id} — {b.label}
            </option>
          ))}
        </select>
      </label>

      <div className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">icon</span>
        <IconPicker value={state.icon_key} onChange={(k) => onChange({ ...state, icon_key: k })} />
      </div>

      <div className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">sensors</span>
        <SensorPicker value={state.sensors} onChange={(s) => onChange({ ...state, sensors: s })} />
      </div>

      <label className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
          matchers (one regex per line)
        </span>
        <textarea
          value={state.matchersText}
          onChange={(e) => onChange({ ...state, matchersText: e.target.value })}
          rows={3}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
          placeholder={'^tank\\b\n.*armoured.*'}
        />
      </label>

      {error && (
        <div className="text-xs text-red-300 bg-red-900/30 border border-red-800 rounded px-2 py-1.5 font-mono whitespace-pre-wrap">
          {error}
        </div>
      )}

      {conflict && (
        <div className="text-xs text-amber-200 bg-amber-900/30 border border-amber-800 rounded px-2 py-1.5">
          <div className="font-mono mb-1">
            {conflict.affected} detections are tagged to this branch.
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={forceDelete}
              onChange={(e) => setForceDelete(e.target.checked)}
            />
            <span className="font-mono text-[11px] uppercase tracking-wider">
              Force delete (reassign affected detections to <code>Other</code>)
            </span>
          </label>
        </div>
      )}

      <div className="flex items-center gap-2 pt-2 border-t border-slate-800">
        <button
          type="button"
          onClick={onSave}
          disabled={busy}
          className="flex items-center gap-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
        >
          <Save className="w-3.5 h-3.5" /> Save
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="flex items-center gap-1 border border-slate-700 hover:border-slate-500 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
        >
          <X className="w-3.5 h-3.5" /> Cancel
        </button>
        {!isNew && onDelete && (
          <button
            type="button"
            onClick={() => {
              if (!conflict && !window.confirm(`Delete branch "${state.id}"?`)) return;
              onDelete(forceDelete);
            }}
            disabled={busy || (conflict !== null && !forceDelete)}
            className="ml-auto flex items-center gap-1 border border-red-800 text-red-300 hover:bg-red-900/30 disabled:opacity-40 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
          >
            <Trash2 className="w-3.5 h-3.5" /> Delete
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Object form
// ---------------------------------------------------------------------------

interface ObjectFormProps {
  state: ObjectFormState;
  isNew: boolean;
  busy: boolean;
  onChange: (next: ObjectFormState) => void;
  onSave: () => void;
  onDelete?: () => void;
  onCancel: () => void;
  error: string | null;
}

function ObjectForm({
  state,
  isNew,
  busy,
  onChange,
  onSave,
  onDelete,
  onCancel,
  error,
}: ObjectFormProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-100">
          {isNew ? 'New object' : 'Edit object'}
        </h3>
        <span className="font-mono text-[10px] text-slate-500">
          branch: {state.branch_id} {!isNew && `· id: ${state.id}`}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">id</span>
          <input
            type="text"
            value={state.id}
            disabled={!isNew}
            onChange={(e) => onChange({ ...state, id: e.target.value })}
            placeholder="e.g. tank_t72"
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">label</span>
          <input
            type="text"
            value={state.label}
            onChange={(e) => onChange({ ...state, label: e.target.value })}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
          prompt (sent to the grounding model)
        </span>
        <textarea
          value={state.prompt}
          onChange={(e) => onChange({ ...state, prompt: e.target.value })}
          rows={3}
          className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
            min gsd (m)
          </span>
          <input
            type="number"
            step="0.1"
            value={state.min_gsd_meters}
            onChange={(e) => onChange({ ...state, min_gsd_meters: e.target.value })}
            placeholder="optional"
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">order</span>
          <input
            type="number"
            value={state.order_index}
            onChange={(e) => onChange({ ...state, order_index: Number(e.target.value || 0) })}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
          />
        </label>
      </div>

      <div className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">icon</span>
        <IconPicker value={state.icon_key} onChange={(k) => onChange({ ...state, icon_key: k })} />
      </div>

      <div className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">sensors</span>
        <SensorPicker value={state.sensors} onChange={(s) => onChange({ ...state, sensors: s })} />
      </div>

      {error && (
        <div className="text-xs text-red-300 bg-red-900/30 border border-red-800 rounded px-2 py-1.5 font-mono whitespace-pre-wrap">
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 pt-2 border-t border-slate-800">
        <button
          type="button"
          onClick={onSave}
          disabled={busy}
          className="flex items-center gap-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
        >
          <Save className="w-3.5 h-3.5" /> Save
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="flex items-center gap-1 border border-slate-700 hover:border-slate-500 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
        >
          <X className="w-3.5 h-3.5" /> Cancel
        </button>
        {!isNew && onDelete && (
          <button
            type="button"
            onClick={() => {
              if (!window.confirm(`Delete object "${state.id}"?`)) return;
              onDelete();
            }}
            disabled={busy}
            className="ml-auto flex items-center gap-1 border border-red-800 text-red-300 hover:bg-red-900/30 disabled:opacity-40 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
          >
            <Trash2 className="w-3.5 h-3.5" /> Delete
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tree pane
// ---------------------------------------------------------------------------

interface TreeNodeProps {
  branch: OntologyBranch;
  depth: number;
  expanded: Set<string>;
  toggle: (id: string) => void;
  selectedKey: string | null;
  onSelectBranch: (id: string) => void;
  onSelectObject: (id: string) => void;
  onAddObject: (branchId: string) => void;
  onAddChildBranch: (parentId: string) => void;
}

function TreeNode({
  branch,
  depth,
  expanded,
  toggle,
  selectedKey,
  onSelectBranch,
  onSelectObject,
  onAddObject,
  onAddChildBranch,
}: TreeNodeProps) {
  const isExpanded = expanded.has(branch.id);
  const isSelected = selectedKey === `branch:${branch.id}`;
  return (
    <div className="border-b border-slate-900/60 last:border-b-0">
      <div
        className={`flex items-center gap-1.5 px-2 py-1.5 ${
          isSelected ? 'bg-slate-800/60' : 'hover:bg-slate-900/60'
        }`}
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <button
          type="button"
          onClick={() => toggle(branch.id)}
          className="text-slate-500 hover:text-slate-200"
          title={isExpanded ? 'collapse' : 'expand'}
        >
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </button>
        <span
          className="inline-block w-2 h-2 rounded-sm shrink-0"
          style={{ background: branch.color || '#666' }}
        />
        <IconRenderer iconKey={branch.icon_key || ''} className="w-3.5 h-3.5 text-slate-300" />
        <button
          type="button"
          onClick={() => onSelectBranch(branch.id)}
          className="flex-1 text-left text-xs font-bold uppercase tracking-wider text-slate-200 truncate"
          title={branch.id}
        >
          {branch.label}
        </button>
        <span className="font-mono text-[9px] text-slate-500">
          {(branch.objects || []).length}o · {(branch.children || []).length}b
        </span>
        <button
          type="button"
          onClick={() => onAddObject(branch.id)}
          className="text-slate-500 hover:text-emerald-300"
          title="Add object to this branch"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          onClick={() => onAddChildBranch(branch.id)}
          className="text-slate-500 hover:text-blue-300"
          title="Add child branch"
        >
          <Plus className="w-3 h-3 rotate-45" />
        </button>
      </div>

      {isExpanded && (
        <div>
          {(branch.objects || []).map((obj) => {
            const sel = selectedKey === `object:${obj.id}`;
            return (
              <div
                key={obj.id}
                className={`flex items-center gap-1.5 px-2 py-1 ${
                  sel ? 'bg-slate-800/60' : 'hover:bg-slate-900/60'
                }`}
                style={{ paddingLeft: 24 + depth * 12 }}
              >
                <span className="w-3.5 h-3.5" />
                <IconRenderer
                  iconKey={obj.icon_key || ''}
                  fallbackBranchKey={branch.icon_key || ''}
                  className="w-3 h-3 text-slate-400"
                />
                <button
                  type="button"
                  onClick={() => onSelectObject(obj.id)}
                  className="flex-1 text-left text-[11px] text-slate-300 truncate"
                  title={obj.id}
                >
                  {obj.label}
                </button>
                <span className="font-mono text-[9px] text-slate-600">{obj.id}</span>
              </div>
            );
          })}
          {(branch.children || []).map((child) => (
            <TreeNode
              key={child.id}
              branch={child}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              selectedKey={selectedKey}
              onSelectBranch={onSelectBranch}
              onSelectObject={onSelectObject}
              onAddObject={onAddObject}
              onAddChildBranch={onAddChildBranch}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Unknown labels triage panel (Step 12)
// ---------------------------------------------------------------------------

function titleCase(input: string): string {
  return input
    .replace(/[_-]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ');
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diffSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 30) return `${Math.round(diffSec / 86400)}d ago`;
  return new Date(t).toISOString().slice(0, 16).replace('T', ' ');
}

interface AssignFormState {
  mode: 'existing' | 'create';
  branchId: string;
  objectId: string;
  newLabel: string;
  newPrompt: string;
  newIconKey: string;
}

interface UnknownRowProps {
  row: UnknownLabel;
  expanded: boolean;
  onToggle: () => void;
  branches: OntologyBranch[];
  onAssigned: () => Promise<void> | void;
}

function UnknownRow({ row, expanded, onToggle, branches, onAssigned }: UnknownRowProps) {
  const defaultBranch = useMemo(() => {
    if (row.suggested_branch_id) return row.suggested_branch_id;
    const flat = flattenBranches(branches);
    const other = flat.find((b) => b.id === 'Other');
    if (other) return 'Other';
    return flat[0]?.id || '';
  }, [branches, row.suggested_branch_id]);

  const [form, setForm] = useState<AssignFormState>(() => ({
    mode: 'create',
    branchId: defaultBranch,
    objectId: '',
    newLabel: titleCase(row.label),
    newPrompt: row.label,
    newIconKey: 'circle_help',
  }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Reset form when row gets re-expanded for a fresh edit.
  useEffect(() => {
    if (expanded) {
      setForm({
        mode: 'create',
        branchId: defaultBranch,
        objectId: '',
        newLabel: titleCase(row.label),
        newPrompt: row.label,
        newIconKey: 'circle_help',
      });
      setErr(null);
    }
  }, [expanded, defaultBranch, row.label]);

  const objectsInBranch = useMemo(() => {
    if (!form.branchId) return [] as OntologyObject[];
    const flat = flattenObjects(branches);
    return flat
      .filter((o) => o.branch_id === form.branchId)
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [branches, form.branchId]);

  const branchOptions = useMemo(() => flattenBranches(branches), [branches]);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (!form.branchId) throw new Error('Pick a branch');
      if (form.mode === 'existing') {
        if (!form.objectId) throw new Error('Pick an object');
        await assignUnknownLabel(row.label, {
          branch_id: form.branchId,
          object_id: form.objectId,
        });
      } else {
        if (!form.newLabel.trim()) throw new Error('Label is required');
        if (!form.newPrompt.trim()) throw new Error('Prompt is required');
        await assignUnknownLabel(row.label, {
          branch_id: form.branchId,
          create_object: {
            label: form.newLabel.trim(),
            prompt: form.newPrompt.trim(),
            icon_key: form.newIconKey || 'circle_help',
          },
        });
      }
      await onAssigned();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={
        expanded
          ? 'border-l-2 border-blue-400 bg-slate-900/60'
          : 'border-l-2 border-transparent hover:bg-slate-900/40'
      }
    >
      <div className="ontology-unknown-grid grid items-center gap-2 px-3 py-2 text-xs">
        <span className="font-mono text-slate-200 truncate" title={row.label}>
          {row.label}
        </span>
        <span className="font-mono text-slate-300 text-right">{row.count}</span>
        <span
          className="font-mono text-[10px] text-slate-400"
          title={row.first_seen || ''}
        >
          {formatRelative(row.first_seen)}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
          {row.layer || '—'}
        </span>
        <button
          type="button"
          onClick={onToggle}
          className={`justify-self-end font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded border transition ${
            expanded
              ? 'border-blue-500 text-blue-200 bg-blue-500/20'
              : 'border-slate-700 text-slate-300 hover:border-slate-500'
          }`}
        >
          {expanded ? 'Close' : 'Assign'}
        </button>
      </div>

      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-slate-800 bg-slate-950/40 space-y-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setForm((s) => ({ ...s, mode: 'existing' }))}
              className={`font-mono text-[10px] uppercase tracking-wider px-3 py-1 rounded border ${
                form.mode === 'existing'
                  ? 'border-blue-400 bg-blue-500/20 text-blue-200'
                  : 'border-slate-700 text-slate-400 hover:text-slate-100'
              }`}
            >
              Assign to existing
            </button>
            <button
              type="button"
              onClick={() => setForm((s) => ({ ...s, mode: 'create' }))}
              className={`font-mono text-[10px] uppercase tracking-wider px-3 py-1 rounded border ${
                form.mode === 'create'
                  ? 'border-blue-400 bg-blue-500/20 text-blue-200'
                  : 'border-slate-700 text-slate-400 hover:text-slate-100'
              }`}
            >
              Create new object
            </button>
          </div>

          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
              branch
            </span>
            <select
              value={form.branchId}
              onChange={(e) =>
                setForm((s) => ({ ...s, branchId: e.target.value, objectId: '' }))
              }
              className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
            >
              <option value="">(pick a branch)</option>
              {branchOptions.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.id} — {b.label}
                </option>
              ))}
            </select>
          </label>

          {form.mode === 'existing' ? (
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                object
              </span>
              <select
                value={form.objectId}
                onChange={(e) => setForm((s) => ({ ...s, objectId: e.target.value }))}
                className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
                disabled={!form.branchId}
              >
                <option value="">
                  {form.branchId ? '(pick an object)' : '(pick a branch first)'}
                </option>
                {objectsInBranch.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label} — {o.id}
                  </option>
                ))}
              </select>
              {form.branchId && objectsInBranch.length === 0 && (
                <span className="font-mono text-[10px] text-amber-300/80">
                  No objects in this branch yet — switch to “Create new object”.
                </span>
              )}
            </label>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                    label
                  </span>
                  <input
                    type="text"
                    value={form.newLabel}
                    onChange={(e) => setForm((s) => ({ ...s, newLabel: e.target.value }))}
                    className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                    prompt
                  </span>
                  <input
                    type="text"
                    value={form.newPrompt}
                    onChange={(e) => setForm((s) => ({ ...s, newPrompt: e.target.value }))}
                    className="bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs font-mono"
                  />
                </label>
              </div>
              <div className="flex flex-col gap-1">
                <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                  icon
                </span>
                <IconPicker
                  value={form.newIconKey}
                  onChange={(k) => setForm((s) => ({ ...s, newIconKey: k }))}
                />
              </div>
            </>
          )}

          {err && (
            <div className="text-xs text-red-300 bg-red-900/30 border border-red-800 rounded px-2 py-1.5 font-mono whitespace-pre-wrap">
              {err}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={busy}
              className="flex items-center gap-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
            >
              <Check className="w-3.5 h-3.5" />
              {form.mode === 'existing' ? 'Confirm' : 'Create + assign'}
            </button>
            <button
              type="button"
              onClick={onToggle}
              disabled={busy}
              className="flex items-center gap-1 border border-slate-700 hover:border-slate-500 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
            >
              <X className="w-3.5 h-3.5" /> Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

type OntologyAdminProps = {
  /** Optional cross-workspace navigation hooks; when set, "Open on GEOINT" /
   *  "Open in FMV" buttons appear next to recent-instance rows. */
  onOpenDetectionOnMap?: (detectionId: number, className?: string) => void;
  onOpenDetectionInFmv?: (detectionId: number) => void;
};

export default function OntologyAdmin({
  onOpenDetectionOnMap,
  onOpenDetectionInFmv,
}: OntologyAdminProps = {}) {
  const { tree, branches, branchById, objectsById, isLoading, error: loadError, refresh } = useOntology();
  const [mode, setMode] = useState<EditorMode>({ kind: 'none' });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ type: 'detections'; affected: number } | null>(null);

  const [branchForm, setBranchForm] = useState<BranchFormState>(emptyBranch(null));
  const [objectForm, setObjectForm] = useState<ObjectFormState>(emptyObject(''));

  // Step 12 — unknown labels triage
  const [unknowns, setUnknowns] = useState<UnknownLabel[]>([]);
  const [unknownsLoading, setUnknownsLoading] = useState(false);
  const [unknownsError, setUnknownsError] = useState<string | null>(null);
  const [expandedLabel, setExpandedLabel] = useState<string | null>(null);
  const [showAllUnknowns, setShowAllUnknowns] = useState(false);
  const [unknownsToast, setUnknownsToast] = useState<string | null>(null);

  const UNKNOWNS_FETCH_LIMIT = 200;
  const UNKNOWNS_INITIAL_DISPLAY = 50;

  const fetchUnknowns = useCallback(async () => {
    setUnknownsLoading(true);
    setUnknownsError(null);
    try {
      const data = await listUnknownLabels({ limit: UNKNOWNS_FETCH_LIMIT });
      setUnknowns(data.unknown_labels || []);
    } catch (e: any) {
      setUnknownsError(e?.message || String(e));
    } finally {
      setUnknownsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUnknowns();
  }, [fetchUnknowns]);

  // Auto-clear toast after a few seconds
  useEffect(() => {
    if (!unknownsToast) return;
    const id = setTimeout(() => setUnknownsToast(null), 4000);
    return () => clearTimeout(id);
  }, [unknownsToast]);

  // When the loaded tree updates and the selection points at an existing
  // entity, refresh the form contents so PATCH responses are reflected.
  useEffect(() => {
    if (mode.kind === 'branch') {
      const b = branchById.get(mode.id);
      if (b) setBranchForm(branchToForm(b));
      else setMode({ kind: 'none' });
    } else if (mode.kind === 'object') {
      const o = objectsById.get(mode.id);
      if (o) setObjectForm(objectToForm(o));
      else setMode({ kind: 'none' });
    }
    // intentionally exclude form state to avoid clobbering user edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree?.version_id, mode.kind, (mode as any).id]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectBranch = (id: string) => {
    const b = branchById.get(id);
    if (!b) return;
    setMode({ kind: 'branch', id });
    setBranchForm(branchToForm(b));
    setFormError(null);
    setConflict(null);
  };

  const selectObject = (id: string) => {
    const o = objectsById.get(id);
    if (!o) return;
    setMode({ kind: 'object', id });
    setObjectForm(objectToForm(o));
    setFormError(null);
  };

  const startNewBranch = (parentId: string | null) => {
    setMode({ kind: 'new-branch', parentId });
    setBranchForm(emptyBranch(parentId));
    setFormError(null);
    setConflict(null);
  };

  const startNewObject = (branchId: string) => {
    setMode({ kind: 'new-object', branchId });
    setObjectForm(emptyObject(branchId));
    setFormError(null);
    if (!expanded.has(branchId)) toggle(branchId);
  };

  const cancelEdit = () => {
    setMode({ kind: 'none' });
    setFormError(null);
    setConflict(null);
  };

  const buildBranchPayload = (form: BranchFormState, isNew: boolean): BranchPayload => {
    const matchers = form.matchersText
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    const payload: BranchPayload = {
      label: form.label,
      color: form.color || null,
      short: form.short || null,
      icon_key: form.icon_key || null,
      matchers,
      sensors: form.sensors,
      order_index: form.order_index,
    };
    if (isNew) {
      payload.id = form.id;
      payload.parent_id = form.parent_id;
    } else {
      payload.parent_id = form.parent_id;
    }
    return payload;
  };

  const buildObjectPayload = (form: ObjectFormState, isNew: boolean): ObjectPayload => {
    const payload: ObjectPayload = {
      label: form.label,
      prompt: form.prompt,
      sensors: form.sensors,
      icon_key: form.icon_key || null,
      order_index: form.order_index,
      min_gsd_meters: form.min_gsd_meters === '' ? null : Number(form.min_gsd_meters),
    };
    if (isNew) {
      payload.id = form.id;
      payload.branch_id = form.branch_id;
    }
    return payload;
  };

  const saveBranch = async () => {
    setBusy(true);
    setFormError(null);
    try {
      const isNew = mode.kind === 'new-branch';
      const payload = buildBranchPayload(branchForm, isNew);
      let saved: any;
      if (isNew) {
        saved = await createBranch(payload);
      } else {
        saved = await updateBranch(branchForm.id, payload);
      }
      refresh();
      // Re-target the selection at the just-saved row so subsequent edits work.
      setMode({ kind: 'branch', id: saved.id || branchForm.id });
    } catch (e: any) {
      setFormError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveObject = async () => {
    setBusy(true);
    setFormError(null);
    try {
      const isNew = mode.kind === 'new-object';
      const payload = buildObjectPayload(objectForm, isNew);
      let saved: any;
      if (isNew) {
        saved = await createObject(payload);
      } else {
        saved = await updateObject(objectForm.id, payload);
      }
      refresh();
      setMode({ kind: 'object', id: saved.id || objectForm.id });
    } catch (e: any) {
      setFormError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeBranch = async (force: boolean) => {
    setBusy(true);
    setFormError(null);
    try {
      await deleteBranch(branchForm.id, force);
      refresh();
      setMode({ kind: 'none' });
      setConflict(null);
    } catch (e: any) {
      // Inspect 409 conflict for detections.
      const detail = e?.detail;
      if (e?.status === 409 && detail && detail.error === 'branch_has_detections') {
        setConflict({ type: 'detections', affected: Number(detail.affected_detections || 0) });
        setFormError(
          `Branch is still referenced by ${detail.affected_detections} detection(s). ` +
            `Tick the "force delete" box to reassign them to "Other".`,
        );
      } else {
        setFormError(e?.message || String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const removeObject = async () => {
    setBusy(true);
    setFormError(null);
    try {
      await deleteObject(objectForm.id);
      refresh();
      setMode({ kind: 'none' });
    } catch (e: any) {
      setFormError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const selectedKey = useMemo(() => {
    if (mode.kind === 'branch') return `branch:${mode.id}`;
    if (mode.kind === 'object') return `object:${mode.id}`;
    return null;
  }, [mode]);

  return (
    <div className="ontology-admin w-full flex-1 min-h-0 bg-slate-950 text-slate-200 overflow-auto">
      <div className="max-w-6xl mx-auto p-6 flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-slate-800 pb-3">
          <h2 className="text-xl font-bold uppercase tracking-wider">Ontology Admin</h2>
          <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-1 rounded border border-slate-700 text-slate-300">
            v {tree?.version_id ?? '—'}
          </span>
          {isLoading && (
            <span className="font-mono text-[10px] text-slate-500">loading…</span>
          )}
          {loadError && (
            <span className="font-mono text-[10px] text-red-300">{loadError.message}</span>
          )}
          <button
            type="button"
            onClick={() => refresh()}
            className="ml-auto flex items-center gap-1 border border-slate-700 hover:border-slate-500 rounded px-3 py-1.5 text-xs font-bold uppercase tracking-wider"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>

        {/* Two-pane layout */}
        <div className="ontology-split grid grid-cols-1 gap-4">
          {/* Tree */}
          <div className="border border-slate-800 rounded bg-slate-950/40 flex flex-col max-h-[70vh]">
            <div className="px-2 py-2 border-b border-slate-800 flex items-center gap-2">
              <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                Tree
              </span>
              <button
                type="button"
                onClick={() => startNewBranch(null)}
                className="ml-auto flex items-center gap-1 border border-slate-700 hover:border-slate-500 rounded px-2 py-1 text-[10px] font-bold uppercase tracking-wider"
              >
                <Plus className="w-3 h-3" /> Root branch
              </button>
            </div>
            <div className="overflow-y-auto">
              {branches.length === 0 && !isLoading && (
                <div className="px-3 py-4 font-mono text-[10px] text-slate-500 italic">
                  No branches yet. Create the first one with “Root branch”.
                </div>
              )}
              {branches.map((b) => (
                <TreeNode
                  key={b.id}
                  branch={b}
                  depth={0}
                  expanded={expanded}
                  toggle={toggle}
                  selectedKey={selectedKey}
                  onSelectBranch={selectBranch}
                  onSelectObject={selectObject}
                  onAddObject={startNewObject}
                  onAddChildBranch={(pid) => startNewBranch(pid)}
                />
              ))}
            </div>
          </div>

          {/* Editor */}
          <div className="border border-slate-800 rounded bg-slate-950/40 p-4 min-h-[40vh]">
            {mode.kind === 'none' && (
              <div className="text-xs font-mono text-slate-500 italic">
                Select a branch or object on the left to edit, or use the “+” buttons to create a
                new one.
              </div>
            )}
            {(mode.kind === 'branch' || mode.kind === 'new-branch') && (
              <BranchForm
                state={branchForm}
                isNew={mode.kind === 'new-branch'}
                branches={branches}
                busy={busy}
                onChange={setBranchForm}
                onSave={saveBranch}
                onDelete={mode.kind === 'branch' ? removeBranch : undefined}
                onCancel={cancelEdit}
                error={formError}
                conflict={conflict}
              />
            )}
            {(mode.kind === 'object' || mode.kind === 'new-object') && (
              <ObjectForm
                state={objectForm}
                isNew={mode.kind === 'new-object'}
                busy={busy}
                onChange={setObjectForm}
                onSave={saveObject}
                onDelete={mode.kind === 'object' ? removeObject : undefined}
                onCancel={cancelEdit}
                error={formError}
              />
            )}
            {mode.kind === 'object' && (
              <RecentInstancesPanel
                key={`instances-${mode.id}`}
                objectId={mode.id}
                onOpenOnMap={onOpenDetectionOnMap}
                onOpenInFmv={onOpenDetectionInFmv}
              />
            )}
          </div>
        </div>

        {/* Unknown labels triage (Step 12) */}
        <div className="border border-slate-800 rounded bg-slate-950/40">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-800">
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-100">
              Unknown labels
            </h3>
            <span
              className={`font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded border ${
                unknowns.length > 0
                  ? 'border-amber-700 text-amber-200 bg-amber-900/30'
                  : 'border-slate-700 text-slate-400'
              }`}
            >
              {unknowns.length} pending
            </span>
            {unknownsLoading && (
              <span className="font-mono text-[10px] text-slate-500">loading…</span>
            )}
            {unknownsError && (
              <span className="font-mono text-[10px] text-red-300 truncate" title={unknownsError}>
                {unknownsError}
              </span>
            )}
            {unknownsToast && (
              <span className="font-mono text-[10px] text-emerald-300">{unknownsToast}</span>
            )}
            <button
              type="button"
              onClick={() => fetchUnknowns()}
              disabled={unknownsLoading}
              className="ml-auto flex items-center gap-1 border border-slate-700 hover:border-slate-500 disabled:opacity-40 rounded px-2 py-1 text-[10px] font-bold uppercase tracking-wider"
            >
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>

          {unknowns.length === 0 && !unknownsLoading ? (
            <div className="px-4 py-6 font-mono text-[11px] text-slate-500 italic">
              No unknown labels in the queue. Anything the matchers can’t place will appear here for
              triage.
            </div>
          ) : (
            <>
              <div className="ontology-unknown-grid grid items-center gap-2 px-3 py-2 border-b border-slate-800 font-mono text-[9px] uppercase tracking-wider text-slate-500">
                <span>label</span>
                <span className="text-right">count</span>
                <span>first_seen</span>
                <span>layer</span>
                <span className="text-right">action</span>
              </div>
              <div className="divide-y divide-slate-900/60">
                {(showAllUnknowns
                  ? unknowns
                  : unknowns.slice(0, UNKNOWNS_INITIAL_DISPLAY)
                ).map((row) => (
                  <UnknownRow
                    key={row.label}
                    row={row}
                    expanded={expandedLabel === row.label}
                    onToggle={() =>
                      setExpandedLabel((prev) => (prev === row.label ? null : row.label))
                    }
                    branches={branches}
                    onAssigned={async () => {
                      setExpandedLabel(null);
                      setUnknownsToast(`Assigned “${row.label}”`);
                      await fetchUnknowns();
                      refresh();
                    }}
                  />
                ))}
              </div>
              {!showAllUnknowns && unknowns.length > UNKNOWNS_INITIAL_DISPLAY && (
                <div className="px-3 py-2 border-t border-slate-800 text-center">
                  <button
                    type="button"
                    onClick={() => setShowAllUnknowns(true)}
                    className="font-mono text-[10px] uppercase tracking-wider text-slate-400 hover:text-slate-100"
                  >
                    Show more ({unknowns.length - UNKNOWNS_INITIAL_DISPLAY} more)
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent instances — small panel that lists the latest detections classified
// under this taxonomy object and offers cross-workspace navigation.
// ---------------------------------------------------------------------------

type Instance = {
  id: number;
  class: string;
  confidence?: number;
  acquisition_time?: string;
  fmv_clip_id?: number | null;
  threat_level?: string;
  affiliation?: string;
};

function RecentInstancesPanel({
  objectId,
  onOpenOnMap,
  onOpenInFmv,
}: {
  objectId: string;
  onOpenOnMap?: (detectionId: number, className?: string) => void;
  onOpenInFmv?: (detectionId: number) => void;
}) {
  const [items, setItems] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const lowered = objectId.replace(/_/g, ' ').toLowerCase();
    (async () => {
      try {
        // Query detections filtered to this object's class label. The API
        // already lower-cases on disk, so the object id (e.g. "Tank")
        // matches its stored class "tank" once we map to lower.
        const { data } = await axios.get(`/api/detections`, {
          params: { det_class: lowered, limit: 25 },
        });
        if (cancelled) return;
        setItems(
          (data?.detections || []).map((d: any) => ({
            id: d.id,
            class: d.class,
            confidence: d.confidence,
            acquisition_time: d.acquisition_time,
            fmv_clip_id: d.metadata?.fmv_clip_id ?? null,
            threat_level: d.metadata?.threat_level,
            affiliation: d.metadata?.allegiance,
          })),
        );
      } catch (err: any) {
        if (!cancelled) setError(err?.response?.data?.detail || err?.message || 'failed to load instances');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [objectId]);

  return (
    <div className="mt-6 border-t border-slate-800 pt-4">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="font-mono text-[10.5px] uppercase tracking-widest text-slate-400">
          Recent instances · {items.length}
        </h3>
        {loading && <span className="font-mono text-[10px] text-slate-500">loading…</span>}
      </div>
      {error && (
        <div className="mb-2 border border-red-500 bg-red-500/10 px-3 py-2 font-mono text-[10.5px] text-red-300">
          {error}
        </div>
      )}
      {!loading && items.length === 0 && !error && (
        <div className="font-mono text-[11px] text-slate-500">No detections classified as this object yet.</div>
      )}
      <div className="space-y-1.5">
        {items.map((it) => (
          <div
            key={it.id}
            className="grid grid-cols-[1fr_auto] items-center gap-2 border border-slate-800 bg-slate-900/40 px-3 py-2"
          >
            <div className="min-w-0">
              <div className="text-xs font-medium text-slate-200">DET-{it.id} · {it.class}</div>
              <div className="font-mono text-[10px] text-slate-500">
                {it.acquisition_time ? new Date(it.acquisition_time).toLocaleString() : '—'}
                {it.confidence != null && <> · {Math.round((it.confidence || 0) * 100)}%</>}
                {it.threat_level && <> · {it.threat_level.toUpperCase()}</>}
              </div>
            </div>
            <div className="flex gap-1">
              {onOpenOnMap && (
                <button
                  type="button"
                  className="border border-slate-700 px-2 py-0.5 font-mono text-[10px] text-slate-200 hover:border-sentinel-accent hover:text-sentinel-accent"
                  onClick={() => onOpenOnMap(it.id, it.class)}
                  title="Open on the GEOINT map"
                >
                  MAP
                </button>
              )}
              {onOpenInFmv && it.fmv_clip_id != null && (
                <button
                  type="button"
                  className="border border-slate-700 px-2 py-0.5 font-mono text-[10px] text-slate-200 hover:border-sentinel-accent hover:text-sentinel-accent"
                  onClick={() => onOpenInFmv(it.id)}
                  title="Open in the FMV player"
                >
                  FMV
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
