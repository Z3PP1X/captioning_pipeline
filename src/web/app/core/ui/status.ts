import { ActiveJob } from '../models/job.model';
import { Video } from '../models/video.model';

export type StatusVariant =
  | 'default'
  | 'secondary'
  | 'destructive'
  | 'outline'
  | 'ghost'
  | 'link'
  | 'success'
  | 'warning'
  | 'danger'
  | 'muted'
  | 'info';

export interface StatusMeta {
  variant: StatusVariant;
  icon: string;
  label: string;
  spin?: boolean;
}

const STATUS_META: Readonly<Record<string, StatusMeta>> = {
  new: { variant: 'muted', icon: 'lucideCircleDashed', label: 'New' },
  chunking: { variant: 'muted', icon: 'lucideScissors', label: 'Chunking' },
  chunked: { variant: 'muted', icon: 'lucideScissors', label: 'Chunked' },
  extracting: { variant: 'warning', icon: 'lucideLoader2', label: 'Extracting' },
  extracting_750: { variant: 'warning', icon: 'lucideLoader2', label: 'Extracting' },
  curating: { variant: 'info', icon: 'lucideListChecks', label: 'Curating' },
  curating_300: { variant: 'info', icon: 'lucideListChecks', label: 'Curating' },
  captioning: { variant: 'warning', icon: 'lucideCaptions', label: 'Captioning' },
  exported: { variant: 'success', icon: 'lucideCheck', label: 'Exported' },
  skipped: { variant: 'muted', icon: 'lucideSkipForward', label: 'Skipped' },
  ignored: { variant: 'muted', icon: 'lucideBan', label: 'Ignored' },
};

function activeJobMeta(status: string, activeJob: ActiveJob): StatusMeta {
  const pct = Math.round((activeJob.progress || 0) * 100);
  if (activeJob.type === 'openrouter_caption' || status === 'captioning') {
    return {
      variant: 'warning',
      icon: 'lucideCaptions',
      label: pct > 0 ? `Captioning · ${pct}%` : 'Captioning',
    };
  }
  return {
    variant: 'warning',
    icon: 'lucideLoader2',
    label: pct > 0 ? `Extracting · ${pct}%` : 'Extracting',
    spin: true,
  };
}

export function videoStatusMeta(
  video: Pick<Video, 'status' | 'broken' | 'broken_reason'>,
  activeJob?: ActiveJob | null,
): StatusMeta {
  if (video.broken) {
    const reason = video.broken_reason ? `Broken: ${video.broken_reason}` : 'Broken';
    return { variant: 'danger', icon: 'lucideAlertTriangle', label: reason };
  }

  if (activeJob) return activeJobMeta(video.status, activeJob);

  const known = Object.hasOwn(STATUS_META, video.status) ? STATUS_META[video.status] : undefined;
  return known ? { ...known } : { variant: 'muted', icon: 'lucideCircleDashed', label: video.status || 'New' };
}

export const ALL_VIDEO_STATUSES = [
  { value: 'new', label: 'New' },
  { value: 'chunking', label: 'Chunking' },
  { value: 'chunked', label: 'Chunked' },
  { value: 'extracting', label: 'Extracting' },
  { value: 'extracting_750', label: 'Extracting (750)' },
  { value: 'curating', label: 'Curating' },
  { value: 'curating_300', label: 'Curating (300)' },
  { value: 'captioning', label: 'Captioning' },
  { value: 'exported', label: 'Exported' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'ignored', label: 'Ignored' },
] as const;

export type VideoStatusValue = (typeof ALL_VIDEO_STATUSES)[number]['value'];

export function videoStatusLabel(status: string | null | undefined): string {
  return ALL_VIDEO_STATUSES.find((s) => s.value === status)?.label ?? (status || 'New');
}

/**
 * The status the video's data implies (decision 3: "derived status"), mirroring `reconcile.py`:
 * every approved pick exported -> `exported`; any picks -> `curating_300`; any chunks -> `chunked`; else `new`.
 * `kept_count` stands in for the approved picks (same keep/star/non-rejected pick filter as the server).
 * Manual states (`skipped`, `ignored`) are ignored here: "Restore" moves a skipped video back to this value.
 */
export function derivedVideoStatus(
  video: Pick<Video, 'chunk_count' | 'kept_count' | 'export_count'>,
  chunkCount: number = video.chunk_count ?? 0,
): VideoStatusValue {
  const kept = video.kept_count ?? 0;
  const exported = video.export_count ?? 0;
  if (kept > 0 && exported >= kept) return 'exported';
  if (kept > 0) return 'curating_300';
  if (chunkCount > 0) return 'chunked';
  return 'new';
}
