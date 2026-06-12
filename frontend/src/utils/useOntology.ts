/**
 * Live ontology hook + module-level cache.
 *
 * Replaces the static `defenceOntology.json` import. Components call
 * `useOntology({ sensor })` to fetch a (server-filtered) tree and re-fetch
 * automatically when the backend bumps `version_id`.
 *
 * The cache is shared module-wide so multiple components don't issue
 * duplicate fetches for the same sensor. A single 30s version watcher
 * polls `/api/ontology/version` and re-fetches the current sensor's tree
 * when the version changes.
 */
import { useEffect, useMemo, useState } from 'react';

const API_URL = import.meta.env.VITE_API_URL || '';
const VERSION_POLL_MS = 30_000;

export interface OntologyObject {
  id: string;
  branch_id: string;
  label: string;
  prompt: string;
  sensors: string[];
  min_gsd_meters?: number | null;
  icon_key?: string | null;
  order_index: number;
}

export interface OntologyBranch {
  id: string;
  parent_id?: string | null;
  label: string;
  color?: string | null;
  short?: string | null;
  icon_key?: string | null;
  matchers: string[];
  sensors: string[];
  order_index: number;
  objects: OntologyObject[];
  children: OntologyBranch[];
}

export interface OntologyTree {
  version_id: number;
  branches: OntologyBranch[];
}

const _cacheBySensor = new Map<string, OntologyTree>();
let _versionWatcher: ReturnType<typeof setInterval> | null = null;
let _lastVersion = -1;
const _subscribers = new Set<() => void>();
// Refcount of live subscribers per sensor — lets the watcher recover a sensor
// whose initial fetch failed (subscribed but never cached).
const _sensorRefs = new Map<string, number>();

async function _fetchTree(sensor: string): Promise<OntologyTree> {
  const params = new URLSearchParams();
  if (sensor) params.set('sensor', sensor);
  const url = `${API_URL}/api/ontology${params.toString() ? '?' + params : ''}`;
  const resp = await fetch(url, { credentials: 'include' });
  if (!resp.ok) throw new Error(`ontology fetch failed: ${resp.status}`);
  return resp.json();
}

async function _fetchVersion(): Promise<number> {
  try {
    const resp = await fetch(`${API_URL}/api/ontology/version`, { credentials: 'include' });
    if (!resp.ok) return -1;
    const data = await resp.json();
    return Number(data.version_id ?? -1);
  } catch {
    return -1;
  }
}

function _notifySubscribers() {
  _subscribers.forEach((cb) => cb());
}

function _startVersionWatcher() {
  if (_versionWatcher) return;
  _versionWatcher = setInterval(async () => {
    const v = await _fetchVersion();
    if (v === -1 || v === _lastVersion) return;
    // Re-fetch every cached sensor plus subscribed-but-uncached sensors
    // (failed initial fetch), then notify subscribers. `_lastVersion` is
    // committed only when every refetch succeeded — a failed refetch must
    // retry on the next tick, not wait for the next version bump.
    const sensors = new Set([..._cacheBySensor.keys(), ..._sensorRefs.keys()]);
    let allOk = true;
    await Promise.all(
      Array.from(sensors).map(async (s) => {
        try {
          const t = await _fetchTree(s);
          _cacheBySensor.set(s, t);
        } catch {
          allOk = false; // keep stale entry, retry next tick
        }
      }),
    );
    if (allOk) _lastVersion = v;
    _notifySubscribers();
  }, VERSION_POLL_MS);
}

export interface UseOntologyResult {
  tree: OntologyTree | null;
  branches: OntologyBranch[];
  /** All objects across the tree, indexed by id. */
  objectsById: Map<string, OntologyObject>;
  /** All branches (recursive), indexed by id. */
  branchById: Map<string, OntologyBranch>;
  isLoading: boolean;
  error: Error | null;
  refresh: () => void;
}

export function useOntology(opts: { sensor?: string } = {}): UseOntologyResult {
  const sensor = (opts.sensor || '').toLowerCase();
  const initial = _cacheBySensor.get(sensor) ?? null;
  const [tree, setTree] = useState<OntologyTree | null>(initial);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(!initial);

  useEffect(() => {
    let cancelled = false;
    const cached = _cacheBySensor.get(sensor) ?? null;

    // Sync to whatever is in cache for this sensor on subscribe.
    setTree(cached);

    // Subscribe to global cache updates so we re-render when the version
    // watcher refetches.
    const cb = () => {
      if (cancelled) return;
      const next = _cacheBySensor.get(sensor) ?? null;
      setTree(next);
      // Watcher recovery after a failed initial fetch — clear the stale error.
      if (next) setError(null);
    };
    _subscribers.add(cb);
    _sensorRefs.set(sensor, (_sensorRefs.get(sensor) ?? 0) + 1);

    if (!cached) {
      setIsLoading(true);
      _fetchTree(sensor)
        .then((t) => {
          if (cancelled) return;
          _cacheBySensor.set(sensor, t);
          _lastVersion = Math.max(_lastVersion, Number(t.version_id ?? -1));
          setTree(t);
          setError(null);
          // Notify any other subscribers waiting on the same sensor.
          _notifySubscribers();
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
        })
        .finally(() => {
          // Arm only while still subscribed — arming after the cleanup ran
          // would leave a permanent zero-subscriber poll.
          if (!cancelled) {
            setIsLoading(false);
            _startVersionWatcher();
          }
        });
    } else {
      setIsLoading(false);
      _startVersionWatcher();
    }

    return () => {
      cancelled = true;
      _subscribers.delete(cb);
      const refs = (_sensorRefs.get(sensor) ?? 1) - 1;
      if (refs <= 0) _sensorRefs.delete(sensor);
      else _sensorRefs.set(sensor, refs);
      // Stop polling when nothing is subscribed; the next subscriber will
      // re-arm the watcher in `_startVersionWatcher`. Prevents HMR/navigation
      // from leaking a new interval per reload.
      if (_subscribers.size === 0 && _versionWatcher) {
        clearInterval(_versionWatcher);
        _versionWatcher = null;
      }
    };
  }, [sensor]);

  const objectsById = useMemo(() => {
    const m = new Map<string, OntologyObject>();
    function walk(b: OntologyBranch) {
      for (const o of b.objects || []) m.set(o.id, o);
      for (const c of b.children || []) walk(c);
    }
    if (tree) tree.branches.forEach(walk);
    return m;
  }, [tree]);

  const branchById = useMemo(() => {
    const m = new Map<string, OntologyBranch>();
    function walk(b: OntologyBranch) {
      m.set(b.id, b);
      for (const c of b.children || []) walk(c);
    }
    if (tree) tree.branches.forEach(walk);
    return m;
  }, [tree]);

  const refresh = () => {
    _cacheBySensor.delete(sensor);
    setTree(null);
    setIsLoading(true);
    _fetchTree(sensor)
      .then((t) => {
        _cacheBySensor.set(sensor, t);
        _lastVersion = Math.max(_lastVersion, Number(t.version_id ?? -1));
        setTree(t);
        setError(null);
        _notifySubscribers();
      })
      .catch((e) => setError(e instanceof Error ? e : new Error(String(e))))
      .finally(() => setIsLoading(false));
  };

  return {
    tree,
    branches: tree?.branches ?? [],
    objectsById,
    branchById,
    isLoading,
    error,
    refresh,
  };
}

/**
 * Flatten a tree into a list of all objects (across all branches/depths).
 * Useful for components that need a flat catalog (e.g. id -> object lookup).
 */
export function flattenObjects(branches: OntologyBranch[]): OntologyObject[] {
  const out: OntologyObject[] = [];
  function walk(b: OntologyBranch) {
    for (const o of b.objects || []) out.push(o);
    for (const c of b.children || []) walk(c);
  }
  branches.forEach(walk);
  return out;
}

/**
 * Flatten a tree into all branches (recursive). Includes nested children.
 */
export function flattenBranches(branches: OntologyBranch[]): OntologyBranch[] {
  const out: OntologyBranch[] = [];
  function walk(b: OntologyBranch) {
    out.push(b);
    for (const c of b.children || []) walk(c);
  }
  branches.forEach(walk);
  return out;
}
