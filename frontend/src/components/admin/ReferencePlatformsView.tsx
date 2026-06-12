/**
 * ReferencePlatformsView — Admin · Reference platforms tab.
 *
 * Browses the curated reference-platform database (Plan D) with family/country
 * filters. Selecting a row fetches the detail payload (including reference
 * chips) and renders chip thumbnails via /api/reference-chips/{id}/image.
 *
 * Follows the AlertsView convention: ViewHeader header + an axios-backed
 * load() function + onCount(n) for the NAV badge. Two-column layout (list on
 * the left, detail on the right) collapses to a single column on narrow
 * containers via the page-level container query.
 *
 * axios has `withCredentials = true` set globally in useAuth.ts.
 */

import axios from 'axios';
import { Database, RefreshCw, Sprout } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import ChipImg from '../ChipImg';
import { useEventStream } from '../../hooks/useEventStream';
import { apiErrorMessage } from '../../utils/apiError';
import ViewHeader from './ViewHeader';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

const LIST_LIMIT = 200;

type PlatformSummary = {
  id: string;
  platform_name: string;
  platform_family: string;
  ontology_object_id?: string | null;
  country_of_origin?: string | null;
  role?: string | null;
  view_domains: string[];
  attributes: Record<string, unknown>;
};

type ReferenceChip = {
  id: string;
  chip_path: string;
  source_dataset: string;
  source_url?: string | null;
  license_spdx: string;
  attribution?: string | null;
};

type PlatformDetail = PlatformSummary & {
  chips: ReferenceChip[];
};

type ListResponse = { platforms?: PlatformSummary[]; count?: number; total?: number };

type Props = { onCount: (n: number) => void };

export default function ReferencePlatformsView({ onCount }: Props) {
  const [platforms, setPlatforms] = useState<PlatformSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [familyFilter, setFamilyFilter] = useState('');
  const [countryFilter, setCountryFilter] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PlatformDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [listErr, setListErr] = useState<string | null>(null);
  const [listBusy, setListBusy] = useState(false);

  const load = useCallback(async () => {
    setListBusy(true);
    setListErr(null);
    try {
      const params: Record<string, string | number> = { limit: LIST_LIMIT };
      const fam = familyFilter.trim();
      const ctry = countryFilter.trim();
      if (fam) params.family = fam;
      if (ctry) params.country = ctry;
      const r = await axios.get<ListResponse>(
        `${API_URL}/api/reference-platforms`,
        { params },
      );
      setPlatforms(r.data?.platforms ?? []);
      setTotal(r.data?.total ?? 0);
    } catch (e: any) {
      setListErr(apiErrorMessage(e, 'load failed'));
      setPlatforms([]);
      setTotal(0);
    } finally {
      setListBusy(false);
    }
  }, [familyFilter, countryFilter]);

  // Initial load only; subsequent loads happen via Apply button to keep the
  // filter inputs uncontrolled-ish (analyst types freely without re-querying
  // every keystroke).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void load(); }, []);

  useEffect(() => { onCount(platforms.length); }, [platforms.length, onCount]);

  // Monotonic token guards against out-of-order detail responses — rapid row
  // clicks would otherwise let a slow earlier response overwrite the latest
  // selection's detail.
  const detailReqRef = useRef(0);
  const openPlatform = useCallback(async (id: string) => {
    const token = ++detailReqRef.current;
    setSelectedId(id);
    setDetail(null);
    setDetailErr(null);
    setDetailBusy(true);
    try {
      const r = await axios.get<PlatformDetail>(
        `${API_URL}/api/reference-platforms/${id}`,
      );
      if (detailReqRef.current !== token) return;
      setDetail(r.data);
    } catch (e: any) {
      if (detailReqRef.current !== token) return;
      setDetailErr(apiErrorMessage(e, 'detail load failed'));
    } finally {
      if (detailReqRef.current === token) setDetailBusy(false);
    }
  }, []);

  const applyFilters = () => { void load(); };

  // ---- Re-seed wiring --------------------------------------------------
  // POST /api/admin/reference/seed enqueues a Celery task; progress arrives
  // over WS topic "reference-seed". We aggregate per-dataset rows and stop
  // listening once a "done" event arrives.
  type SeedProgressRow = { dataset: string; platforms: number; chips: number };
  const [seedBusy, setSeedBusy] = useState(false);
  const [seedTaskId, setSeedTaskId] = useState<string | null>(null);
  const [seedErr, setSeedErr] = useState<string | null>(null);
  const [seedNotice, setSeedNotice] = useState<string | null>(null);
  const [seedRows, setSeedRows] = useState<SeedProgressRow[]>([]);
  const [seedDone, setSeedDone] = useState(false);

  // Fallback so seedBusy can't hang forever when no WS event ever arrives
  // (worker down, or an older worker whose idempotency guard returns without
  // publishing). Armed on enqueue, disarmed by the first seed event.
  const seedFallbackRef = useRef<number | null>(null);
  const clearSeedFallback = useCallback(() => {
    if (seedFallbackRef.current != null) {
      window.clearTimeout(seedFallbackRef.current);
      seedFallbackRef.current = null;
    }
  }, []);
  useEffect(() => clearSeedFallback, [clearSeedFallback]);

  useEventStream(
    'reference-seed',
    useCallback(
      (msg: any) => {
        if (!msg || typeof msg !== 'object') return;
        clearSeedFallback();
        if (msg.type === 'started') {
          setSeedRows([]);
          setSeedDone(false);
          setSeedNotice(null);
        } else if (msg.type === 'dataset_progress') {
          setSeedRows((rows) => {
            const next = rows.filter((r) => r.dataset !== msg.dataset);
            next.push({ dataset: msg.dataset, platforms: msg.platforms ?? 0, chips: msg.chips ?? 0 });
            return next;
          });
        } else if (msg.type === 'done') {
          setSeedDone(true);
          setSeedBusy(false);
          if (msg.skipped) {
            setSeedNotice('Already seeded — use Re-seed to force a re-bake.');
          }
          // Refresh the list so the new totals show.
          void load();
        } else if (msg.type === 'error') {
          setSeedErr(`${msg.dataset}: ${msg.detail}`);
          setSeedBusy(false);
        }
      },
      [load, clearSeedFallback],
    ),
  );

  const triggerSeed = useCallback(async (force: boolean) => {
    setSeedBusy(true);
    setSeedErr(null);
    setSeedNotice(null);
    setSeedRows([]);
    setSeedDone(false);
    try {
      const r = await axios.post<{ task_id: string }>(
        `${API_URL}/api/admin/reference/seed`,
        { force, only: [] },
      );
      setSeedTaskId(r.data?.task_id ?? null);
      clearSeedFallback();
      seedFallbackRef.current = window.setTimeout(() => {
        seedFallbackRef.current = null;
        setSeedBusy(false);
        setSeedNotice('No progress event from the worker after 10s — it may already be seeded or the worker may be down. Check worker logs.');
      }, 10_000);
    } catch (e: any) {
      setSeedErr(apiErrorMessage(e, 'seed enqueue failed'));
      setSeedBusy(false);
    }
  }, [clearSeedFallback]);

  return (
    <>
      <ViewHeader
        title="Reference platforms"
        sub={
          total > platforms.length
            ? `Showing ${platforms.length} of ${total} · narrow filters to see specific platforms · /api/reference-platforms`
            : `${platforms.length} loaded · curated reference DB · /api/reference-platforms`
        }
        actions={
          <>
            <button
              className="btn sm"
              type="button"
              onClick={() => void triggerSeed(false)}
              disabled={seedBusy}
              data-tour="admin-reference-seed-button"
              aria-label="Seed reference DB from baked corpora"
              title="Bake reference_platforms from /opt/reference-corpora/. No-op when rows already exist; use 'Re-seed' to force re-bake."
            >
              <Sprout size={12} /> {seedBusy ? 'Seeding…' : 'Seed'}
            </button>
            <button
              className="btn sm"
              type="button"
              onClick={() => void triggerSeed(true)}
              disabled={seedBusy}
              aria-label="Force re-seed reference DB (re-bake even if rows present)"
              title="Force re-bake: existing rows are upserted, embeddings recomputed."
            >
              Re-seed
            </button>
            <button
              className="btn sm"
              type="button"
              onClick={() => void load()}
              disabled={listBusy}
              aria-label="Reload reference platforms"
            >
              <RefreshCw size={12} /> Reload
            </button>
          </>
        }
      />
      <div
        className="reference-platforms-view"
        data-tour="admin-reference-platforms"
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          padding: 18,
          gap: 12,
          containerType: 'inline-size',
          containerName: 'reference-platforms-view',
        }}
      >
        <div
          className="reference-platforms-view-filters"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            alignItems: 'center',
          }}
        >
          <label
            className="mono"
            style={{ fontSize: 10.5, color: 'var(--ink-3)' }}
          >
            FAMILY
          </label>
          <input
            type="text"
            value={familyFilter}
            onChange={(e) => setFamilyFilter(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') applyFilters(); }}
            placeholder="e.g. destroyer"
            style={inputStyle}
            aria-label="Filter by platform family"
          />
          <label
            className="mono"
            style={{ fontSize: 10.5, color: 'var(--ink-3)' }}
          >
            COUNTRY
          </label>
          <input
            type="text"
            value={countryFilter}
            onChange={(e) => setCountryFilter(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') applyFilters(); }}
            placeholder="e.g. USA"
            style={inputStyle}
            aria-label="Filter by country of origin"
          />
          <button
            type="button"
            className="btn sm primary"
            onClick={applyFilters}
            disabled={listBusy}
          >
            Apply
          </button>
        </div>

        {listErr && (
          <div
            className="card"
            role="alert"
            style={{ padding: 14, borderLeft: '3px solid var(--nato-hostile)' }}
          >
            <div className="mono" style={{ fontSize: 11, color: 'var(--nato-hostile)' }}>
              Failed to load reference platforms: {listErr}
            </div>
          </div>
        )}

        {(seedBusy || seedRows.length > 0 || seedErr || seedNotice) && (
          <div
            className="card"
            role="status"
            aria-live="polite"
            style={{
              padding: 12,
              borderLeft: `3px solid ${seedErr ? 'var(--nato-hostile)' : seedDone ? 'var(--ok)' : 'var(--accent)'}`,
            }}
          >
            <div
              className="mono"
              style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 6 }}
            >
              <Sprout size={12} style={{ verticalAlign: 'text-bottom', marginRight: 4 }} />
              {seedDone ? 'SEED COMPLETE' : seedBusy ? 'SEEDING' : 'SEED'}
              {seedTaskId ? ` · task ${seedTaskId.slice(0, 8)}` : ''}
            </div>
            {seedErr && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--nato-hostile)' }}>
                error: {seedErr}
              </div>
            )}
            {seedNotice && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>
                {seedNotice}
              </div>
            )}
            {seedRows.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {seedRows.map((r) => (
                  <li key={r.dataset}>
                    <strong>{r.dataset}</strong>: {r.platforms} platforms, {r.chips} chips
                  </li>
                ))}
              </ul>
            )}
            {seedBusy && seedRows.length === 0 && !seedErr && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                Waiting for worker to start…
              </div>
            )}
          </div>
        )}

        <div
          className="reference-platforms-view-body"
          style={{
            flex: 1,
            minHeight: 0,
            display: 'grid',
            gridTemplateColumns: 'minmax(280px, 1fr) minmax(320px, 1.4fr)',
            gap: 12,
          }}
        >
          <div
            className="reference-platforms-view-list scroll"
            style={{
              minHeight: 0,
              border: '1px solid var(--line)',
              borderRadius: 4,
              background: 'var(--bg-1)',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'auto',
            }}
            role="region"
            aria-label="Reference platforms"
          >
            {!listErr && !listBusy && platforms.length === 0 && (
              <div
                className="mono"
                style={{
                  padding: 14,
                  fontSize: 11,
                  color: 'var(--ink-3)',
                }}
              >
                No reference platforms match the current filters.
              </div>
            )}
            {listBusy && platforms.length === 0 && (
              <div
                className="mono"
                style={{ padding: 14, fontSize: 11, color: 'var(--ink-3)' }}
              >
                Loading…
              </div>
            )}
            {platforms.map((p) => (
              <button
                key={p.id}
                type="button"
                className="reference-platforms-view-row"
                data-selected={selectedId === p.id || undefined}
                onClick={() => void openPlatform(p.id)}
                style={{
                  textAlign: 'left',
                  padding: '10px 12px',
                  border: 0,
                  borderBottom: '1px solid var(--line)',
                  background:
                    selectedId === p.id
                      ? 'color-mix(in oklab, var(--accent) 14%, var(--bg-1))'
                      : 'transparent',
                  color: 'var(--ink-0)',
                  cursor: 'pointer',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 3,
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 500 }}>
                  {p.platform_name}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: 10.5, color: 'var(--ink-3)' }}
                >
                  {p.platform_family}
                  {p.country_of_origin ? ` · ${p.country_of_origin}` : ''}
                  {p.role ? ` · ${p.role}` : ''}
                </span>
              </button>
            ))}
          </div>

          <div
            className="reference-platforms-view-detail scroll"
            style={{
              minHeight: 0,
              border: '1px solid var(--line)',
              borderRadius: 4,
              background: 'var(--bg-1)',
              overflow: 'auto',
              padding: 14,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
            role="region"
            aria-label="Reference platform detail"
            aria-live="polite"
          >
            {!selectedId && (
              <div
                className="mono"
                style={{
                  fontSize: 11,
                  color: 'var(--ink-3)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <Database size={14} aria-hidden />
                Select a platform to view chips and metadata.
              </div>
            )}
            {selectedId && detailBusy && !detail && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                Loading platform detail…
              </div>
            )}
            {detailErr && (
              <div
                className="mono"
                role="alert"
                style={{
                  fontSize: 10.5,
                  color: 'var(--nato-hostile)',
                  padding: '6px 8px',
                  border: '1px solid var(--nato-hostile)',
                }}
              >
                {detailErr}
              </div>
            )}
            {detail && (
              <PlatformDetailCard detail={detail} />
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/* ─── Subcomponents ──────────────────────────────────────────────────── */

function PlatformDetailCard({ detail }: { detail: PlatformDetail }) {
  return (
    <>
      <div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{detail.platform_name}</div>
        <div
          className="mono"
          style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 3 }}
        >
          {detail.platform_family}
          {detail.country_of_origin ? ` · ${detail.country_of_origin}` : ''}
          {detail.role ? ` · ${detail.role}` : ''}
        </div>
      </div>

      <MetadataGrid detail={detail} />

      <div
        className="reference-platforms-view-chips-header mono"
        style={{
          fontSize: 10.5,
          color: 'var(--ink-3)',
          letterSpacing: '.08em',
          textTransform: 'uppercase',
        }}
      >
        Reference chips · {detail.chips.length}
      </div>
      {detail.chips.length === 0 ? (
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          No chips registered for this platform.
        </div>
      ) : (
        <div
          className="reference-platforms-view-chips"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
            gap: 8,
          }}
        >
          {detail.chips.map((chip) => (
            <ChipTile key={chip.id} chip={chip} />
          ))}
        </div>
      )}
    </>
  );
}

function MetadataGrid({ detail }: { detail: PlatformDetail }) {
  const rows: Array<[string, string]> = [];
  rows.push(['ID', detail.id]);
  if (detail.ontology_object_id) rows.push(['Ontology', detail.ontology_object_id]);
  if (detail.view_domains?.length) rows.push(['Views', detail.view_domains.join(', ')]);
  const attrKeys = Object.keys(detail.attributes || {}).slice(0, 8);
  for (const k of attrKeys) {
    const v = (detail.attributes as any)[k];
    rows.push([k, typeof v === 'string' ? v : JSON.stringify(v)]);
  }
  return (
    <div
      className="reference-platforms-view-meta"
      style={{
        display: 'grid',
        gridTemplateColumns: 'max-content 1fr',
        rowGap: 4,
        columnGap: 12,
      }}
    >
      {rows.flatMap(([k, v]) => [
        <div
          key={`${k}-k`}
          className="mono"
          style={{
            fontSize: 10.5,
            color: 'var(--ink-3)',
            letterSpacing: '.04em',
            textTransform: 'uppercase',
          }}
        >
          {k}
        </div>,
        <div
          key={`${k}-v`}
          className="mono"
          style={{
            fontSize: 11,
            color: 'var(--ink-1)',
            wordBreak: 'break-word',
          }}
        >
          {v}
        </div>,
      ])}
    </div>
  );
}

function ChipTile({ chip }: { chip: ReferenceChip }) {
  return (
    <figure
      className="reference-platforms-view-chip"
      style={{
        margin: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        background: 'var(--bg-2)',
        border: '1px solid var(--line)',
        borderRadius: 3,
        padding: 6,
      }}
    >
      <ChipImg
        chipId={chip.id}
        size={84}
        alt={`Reference chip from ${chip.source_dataset}`}
        style={{
          width: '100%',
          background: 'var(--bg-1)',
          border: '1px solid var(--line)',
          borderRadius: 2,
          display: 'block',
        }}
      />
      <figcaption
        className="mono"
        style={{
          fontSize: 9.5,
          color: 'var(--ink-3)',
          lineHeight: 1.3,
          wordBreak: 'break-word',
        }}
        title={chip.attribution ?? undefined}
      >
        <div>{chip.source_dataset}</div>
        <div>{chip.license_spdx}</div>
      </figcaption>
    </figure>
  );
}

const inputStyle: CSSProperties = {
  background: 'var(--bg-2)',
  border: '1px solid var(--line)',
  color: 'var(--ink-0)',
  padding: '6px 10px',
  fontSize: 12,
  fontFamily: 'var(--font-sans)',
  inlineSize: 'min(11rem, 100%)',
};
