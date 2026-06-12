/**
 * Thin wrapper around the Step 6 ontology CRUD endpoints.
 *
 * Each function throws an Error with the server response body when the
 * status is non-2xx so the UI can render the message verbatim.
 */
const API_URL = import.meta.env.VITE_API_URL || '';

export interface BranchPayload {
  id?: string;
  parent_id?: string | null;
  label?: string;
  color?: string | null;
  short?: string | null;
  icon_key?: string | null;
  matchers?: string[];
  sensors?: string[];
  order_index?: number;
}

export interface ObjectPayload {
  id?: string;
  branch_id?: string;
  label?: string;
  prompt?: string;
  sensors?: string[];
  min_gsd_meters?: number | null;
  icon_key?: string | null;
  order_index?: number;
}

async function _send(url: string, method: string, body?: unknown): Promise<any> {
  // credentials: the rest of the app uses axios with withCredentials — bare
  // fetch must match or cross-origin VITE_API_URL deployments lose the session.
  const init: RequestInit = { method, credentials: 'include' };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(url, init);
  const text = await r.text();
  if (!r.ok) {
    // Try to surface the JSON detail if present.
    try {
      const j = JSON.parse(text);
      const detail = j?.detail;
      if (detail && typeof detail === 'object') {
        const err = new Error(JSON.stringify(detail));
        (err as any).status = r.status;
        (err as any).detail = detail;
        throw err;
      }
      const err = new Error(typeof detail === 'string' ? detail : text);
      (err as any).status = r.status;
      throw err;
    } catch (e) {
      if ((e as any).status) throw e;
      const err = new Error(text || `HTTP ${r.status}`);
      (err as any).status = r.status;
      throw err;
    }
  }
  return text ? JSON.parse(text) : null;
}

export async function createBranch(body: BranchPayload): Promise<any> {
  return _send(`${API_URL}/api/ontology/branches`, 'POST', body);
}

export async function updateBranch(id: string, body: BranchPayload): Promise<any> {
  return _send(`${API_URL}/api/ontology/branches/${encodeURIComponent(id)}`, 'PATCH', body);
}

export async function deleteBranch(id: string, force = false): Promise<any> {
  const qs = force ? '?force=true' : '';
  return _send(`${API_URL}/api/ontology/branches/${encodeURIComponent(id)}${qs}`, 'DELETE');
}

export async function createObject(body: ObjectPayload): Promise<any> {
  return _send(`${API_URL}/api/ontology/objects`, 'POST', body);
}

export async function updateObject(id: string, body: ObjectPayload): Promise<any> {
  return _send(`${API_URL}/api/ontology/objects/${encodeURIComponent(id)}`, 'PATCH', body);
}

export async function deleteObject(id: string): Promise<any> {
  return _send(`${API_URL}/api/ontology/objects/${encodeURIComponent(id)}`, 'DELETE');
}

// ---------------------------------------------------------------------------
// Unknown labels triage (Step 12)
// ---------------------------------------------------------------------------

export interface UnknownLabel {
  label: string;
  layer: string | null;
  first_seen: string | null;
  last_seen: string | null;
  count: number;
  suggested_branch_id: string | null;
}

export interface UnknownLabelAssignBody {
  branch_id: string;
  object_id?: string;
  create_object?: {
    label: string;
    prompt: string;
    icon_key?: string;
  };
}

export async function listUnknownLabels(
  opts: { limit?: number; since?: string } = {},
): Promise<{ unknown_labels: UnknownLabel[] }> {
  const params = new URLSearchParams();
  if (opts.limit) params.set('limit', String(opts.limit));
  if (opts.since) params.set('since', opts.since);
  const qs = params.toString();
  const r = await fetch(`${API_URL}/api/ontology/unknown-labels${qs ? '?' + qs : ''}`, {
    credentials: 'include',
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function assignUnknownLabel(
  label: string,
  body: UnknownLabelAssignBody,
): Promise<any> {
  const r = await fetch(
    `${API_URL}/api/ontology/unknown-labels/${encodeURIComponent(label)}/assign`,
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
