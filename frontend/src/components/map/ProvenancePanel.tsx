/**
 * Map+ Provenance tab — surfaces the existing metadata that ties a detection
 * back to the raster + model + taxonomy version that produced it.
 */

import { Database, Layers, Tag } from 'lucide-react';
import { EmbeddingBadge, ModalityBadge, Panel } from '../atoms';

export default function ProvenancePanel({ selectedDetection }: { selectedDetection: any | null }) {
  if (!selectedDetection) {
    return (
      <div style={{ padding: 14 }}>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          Select a detection to view its provenance.
        </div>
      </div>
    );
  }
  const p = selectedDetection.properties || {};
  const meta = p.metadata || {};
  const modality = (meta.modality || meta.sensor || 'rgb') as any;
  const embedding = (meta.embedding_head || (meta.embedding ? 'sat' : 'none')) as any;

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Panel
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Database size={13} /> Source raster
          </span>
        }
        sub={meta.taxonomy_version ? `taxonomy ${meta.taxonomy_version}` : ''}
      >
        <Kv label="Source COG" value={meta.source_cog || p.pass_name || '—'} mono />
        <Kv label="Pass ID" value={p.pass_id ? `pass-${p.pass_id}` : '—'} mono />
        <Kv label="Acquisition" value={p.acquisition_time ? new Date(p.acquisition_time).toLocaleString() : '—'} />
        <Kv label="Chip ID" value={meta.chip_id || meta.chip || '—'} mono />
        <Kv label="Coverage" value={meta.coverage_fraction != null ? `${Math.round(Number(meta.coverage_fraction) * 100)}%` : '—'} />
      </Panel>

      <Panel
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Layers size={13} /> Model + sensor
          </span>
        }
      >
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <ModalityBadge m={modality} />
          {meta.embedding ? <EmbeddingBadge kind={embedding} /> : null}
          {meta.uses_multiplex && (
            <span
              className="mono"
              style={{
                fontSize: 9.5,
                padding: '2px 7px',
                color: 'var(--accent)',
                border: '1px solid var(--accent)',
                borderRadius: 2,
              }}
            >
              MULTIPLEX
            </span>
          )}
        </div>
        <Kv label="Model version" value={meta.model_version || '—'} mono />
        <Kv label="Original class" value={meta.original_class || p.class || '—'} mono />
        <Kv label="Parent class" value={meta.parent_class || p.parent_class || '—'} mono />
        <Kv
          label="Confidence"
          value={
            meta.calibrated_confidence != null
              ? `${Math.round(Number(meta.calibrated_confidence) * 100)}% · raw ${Math.round(Number(p.confidence || 0) * 100)}%`
              : `${Math.round(Number(p.confidence || 0) * 100)}%`
          }
          mono
        />
        <Kv label="Threshold profile" value={meta.threshold_profile || '—'} mono />
        <Kv label="Class threshold" value={meta.class_threshold != null ? Number(meta.class_threshold).toFixed(2) : '—'} mono />
      </Panel>

      <Panel
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Tag size={13} /> Taxonomy
          </span>
        }
      >
        <Kv label="Branch" value={meta.branch_id || '—'} mono />
        <Kv label="Taxonomy version" value={meta.taxonomy_version || '—'} mono />
        <Kv label="Review status" value={meta.review_status || p.review_status || 'review_candidate'} mono />
        <Kv label="Assessment" value={meta.assessment_status || p.assessment_status || 'unconfirmed'} mono />
        <Kv label="Evidence" value={(meta.evidence || p.evidence || []).join(' · ') || '—'} />
        {Array.isArray(meta.prithvi_labels) && meta.prithvi_labels.length > 0 && (
          <Kv label="Prithvi labels" value={meta.prithvi_labels.join(', ')} mono />
        )}
      </Panel>
    </div>
  );
}

function Kv({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div
      className="provenance-kv"
      style={{
        display: 'grid',
        gridTemplateColumns: '120px 1fr',
        gap: 8,
        alignItems: 'baseline',
        padding: '4px 0',
      }}
    >
      <span className="label-mono">{label}</span>
      <span
        className={mono ? 'mono' : undefined}
        style={{ fontSize: mono ? 11.5 : 12.5, color: 'var(--ink-0)', wordBreak: 'break-word' }}
      >
        {value || '—'}
      </span>
    </div>
  );
}
