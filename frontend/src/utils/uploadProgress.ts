export interface UploadJob {
  id: number;
  upload_id: string;
  filename: string;
  file_path?: string;
  media_type: string;
  status: string;
  celery_task_id?: string;
  metadata?: Record<string, any> | string;
  updated_at?: string;
  created_at?: string;
}

export function uploadMetadata(job?: UploadJob | null): Record<string, any> {
  if (!job?.metadata) return {};
  if (typeof job.metadata === 'string') {
    try {
      return JSON.parse(job.metadata);
    } catch {
      return {};
    }
  }
  return job.metadata;
}

export function uploadProgress(job?: UploadJob | null): number {
  if (!job) return 0;
  const metadata = uploadMetadata(job);
  if (isUploadBackgroundActive(job)) {
    const llmProgress = Number(metadata.llm_progress);
    return Number.isFinite(llmProgress) ? Math.max(0, Math.min(100, llmProgress)) : 0;
  }
  if (job.status === 'ready') return 100;
  if (job.status === 'failed') return 100;

  const progress = Number(metadata.progress);
  if (Number.isFinite(progress)) return Math.max(0, Math.min(100, progress));
  
  if (job.status === 'processing') return 15;
  if (job.status === 'queued') return 5;
  return 0;
}

export function uploadStage(job?: UploadJob | null): string {
  if (!job) return 'idle';
  const metadata = uploadMetadata(job);
  if (isUploadBackgroundActive(job)) {
    return String(metadata.llm_stage || 'llm classification').replace(/_/g, ' ');
  }
  return String(metadata.stage || job.status || 'stored').replace(/_/g, ' ');
}

export function uploadMessage(job?: UploadJob | null): string {
  if (!job) return '';
  const metadata = uploadMetadata(job);
  if (isUploadBackgroundActive(job)) {
    return String(metadata.llm_message || 'Imagery ready; LLM enrichment running.');
  }
  return String(metadata.message || metadata.error || job.status || '');
}

export function uploadProgressClass(job?: UploadJob | null): string {
  if (job?.status === 'failed') return 'bg-rose-500';
  if (isUploadBackgroundActive(job)) return 'bg-violet-400';
  if (job?.status === 'ready') return 'bg-emerald-400';
  return 'bg-blue-400';
}

export function isUploadBackgroundActive(job?: UploadJob | null): boolean {
  if (!job || job.status === 'failed') return false;
  const metadata = uploadMetadata(job);
  const status = String(metadata.llm_status || '').toLowerCase();
  if (!['queued', 'running', 'processing'].includes(status)) return false;
  const progress = Number(metadata.llm_progress);
  return !Number.isFinite(progress) || progress < 100;
}

export function isUploadActive(job?: UploadJob | null): boolean {
  return !!job && (!['ready', 'failed'].includes(job.status) || isUploadBackgroundActive(job));
}
