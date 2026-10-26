import { ApiError } from '../services/api-error';
import { RESELECT_AXES, ReselectAxis, ReselectReport, ReselectRequest } from '../models/scoring.model';

/** Reselect dialog model. `null` = leave the knob to the server default (empty input). */
export interface ReselectFormModel {
  target_count: number | null;
  diversity: number | null;
  elasticity: number | null;
  per_chunk_floor: number | null;
  per_chunk_cap: number | null;
}

export type ReselectField = keyof ReselectFormModel;

interface Rule {
  label: string;
  min: number;
  max: number;
  integer: boolean;
}

/** Same ranges as `ReselectRequest` in src/pipeline/api.py (pydantic `ge`/`le`). */
export const RESELECT_RULES: Record<ReselectField, Rule> = {
  target_count: { label: 'Target picks', min: 1, max: 5000, integer: true },
  diversity: { label: 'Diversity', min: 0, max: 1, integer: false },
  elasticity: { label: 'Elasticity', min: 0, max: 1, integer: false },
  per_chunk_floor: { label: 'Per-chunk floor', min: 0, max: 5000, integer: true },
  per_chunk_cap: { label: 'Per-chunk cap', min: 0, max: 5000, integer: true },
};

export const RESELECT_FIELDS = Object.keys(RESELECT_RULES) as ReselectField[];

function isSet(v: number | null | undefined): v is number {
  return v !== null && v !== undefined && !(typeof v === 'number' && Number.isNaN(v));
}

/** Type/integer/range message for one knob, or null when the value is empty or valid. */
function fieldError(key: ReselectField, v: number | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  const rule = RESELECT_RULES[key];
  if (typeof v !== 'number' || !Number.isFinite(v)) return `${rule.label} must be a number.`;
  if (rule.integer && !Number.isInteger(v)) return `${rule.label} must be a whole number.`;
  if (v < rule.min || v > rule.max) return `${rule.label} must be between ${rule.min} and ${rule.max}.`;
  return null;
}

/** Floor above cap (only when both knobs are individually valid). */
function floorCapError(
  model: ReselectFormModel,
  errors: Partial<Record<ReselectField, string>>,
): string | null {
  const { per_chunk_floor: floor, per_chunk_cap: cap } = model;
  if (errors.per_chunk_floor || errors.per_chunk_cap) return null;
  return isSet(floor) && isSet(cap) && floor > cap ? 'Per-chunk cap must be at least the floor.' : null;
}

/** Floors of all chunks above the target (only when both knobs are individually valid). */
function floorTargetError(
  model: ReselectFormModel,
  errors: Partial<Record<ReselectField, string>>,
  chunkCount: number,
): string | null {
  const { per_chunk_floor: floor, target_count: target } = model;
  if (errors.per_chunk_floor || errors.target_count) return null;
  if (!isSet(floor) || !isSet(target) || chunkCount <= 0 || floor * chunkCount <= target) return null;
  return `A floor of ${floor} in ${chunkCount} chunks needs at least ${floor * chunkCount} picks (target ${target}).`;
}

/**
 * One message per invalid field (empty object = valid). Mirrors the server's 422 ranges and the obvious 400
 * cases (floor above cap, floors of all chunks above the target) so most infeasible knobs never reach the API.
 */
export function reselectFormErrors(
  model: ReselectFormModel,
  chunkCount = 0,
): Partial<Record<ReselectField, string>> {
  const errors: Partial<Record<ReselectField, string>> = {};
  for (const key of RESELECT_FIELDS) {
    const message = fieldError(key, model[key]);
    if (message) errors[key] = message;
  }
  const capError = floorCapError(model, errors);
  if (capError) errors.per_chunk_cap = capError;
  const floorError = floorTargetError(model, errors, chunkCount);
  if (floorError) errors.per_chunk_floor = floorError;
  return errors;
}

/** Request body from a valid model: empty knobs are omitted so the server applies its defaults. */
export function buildReselectRequest(model: ReselectFormModel): ReselectRequest {
  const body: ReselectRequest = {};
  for (const key of RESELECT_FIELDS) {
    const v = model[key];
    if (isSet(v)) body[key] = v;
  }
  return body;
}

/** Axis cap inputs: share 0-1 per axis, `null` = no cap. */
export type AxisCapsModel = Record<ReselectAxis, number | null>;

export const AXIS_LABELS: Record<ReselectAxis, string> = {
  multi_person: 'Multiple people',
  solo: 'One person',
  equipment: 'Tools / equipment',
  face: 'Face visible',
};

export function axisCapErrors(caps: AxisCapsModel): Partial<Record<ReselectAxis, string>> {
  const errors: Partial<Record<ReselectAxis, string>> = {};
  for (const axis of RESELECT_AXES) {
    const v = caps[axis];
    if (v === null || v === undefined) continue;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1) {
      errors[axis] = `${AXIS_LABELS[axis]} cap must be a share between 0 and 1.`;
    }
  }
  return errors;
}

/** Only the axes with a value; an empty object explicitly turns stored caps off. */
export function buildAxisCaps(caps: AxisCapsModel): Partial<Record<ReselectAxis, number>> {
  const out: Partial<Record<ReselectAxis, number>> = {};
  for (const axis of RESELECT_AXES) {
    const v = caps[axis];
    if (isSet(v)) out[axis] = v;
  }
  return out;
}

/** Comma-separated count parts of the reselect toast. */
function reselectSummaryParts(report: ReselectReport): string[] {
  const parts = [
    `${report.promoted?.length ?? 0} promoted`,
    `${report.demoted?.length ?? 0} demoted`,
    `${report.locked ?? 0} locked (starred/exported)`,
  ];
  if (report.dup_skipped) parts.push(`${report.dup_skipped} near-duplicates skipped`);
  if (report.cross_dataset?.enabled && report.cross_dataset.blocked) {
    parts.push(`${report.cross_dataset.blocked} already in the dataset from other videos`);
  }
  const capsPart = axisOverflowPart(report);
  if (capsPart) parts.push(capsPart);
  return parts;
}

/** "caps exceeded for ..." when any axis cap overflowed, else null. */
function axisOverflowPart(report: ReselectReport): string | null {
  const overflow = Object.entries(report.axis_caps ?? {}).filter(([, a]) => (a?.overflow ?? 0) > 0);
  if (!overflow.length) return null;
  return `caps exceeded for ${overflow.map(([axis]) => AXIS_LABELS[axis as ReselectAxis] ?? axis).join(', ')}`;
}

/** Toast copy for a finished reselect. */
export function formatReselectSummary(report: ReselectReport): { title: string; message: string; isWarning: boolean } {
  const parts = reselectSummaryParts(report);
  let message = `${report.picks_count} of ${report.target_count} picks: ${parts.join(', ')}.`;
  if (report.shortfall > 0) {
    message += ` Shortfall: ${report.shortfall} - not enough eligible frames for the target.`;
  }
  if (report.learned_weights_active) message += ' Learned weights were blended in.';
  return {
    title: report.shortfall > 0 ? 'Reselected with a shortfall' : 'Picks reselected',
    message,
    isWarning: report.shortfall > 0,
  };
}

/** Readable error for the reselect call (409 busy, 400 infeasible, 422 range, 404 video). */
export function reselectErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 409:
        return 'This video has a queued or running job. Wait for it to finish (or cancel it), then reselect.';
      case 400:
        return `These settings cannot be met: ${err.message}`;
      case 422:
        return 'A value is out of range (target 1-5000, diversity and elasticity 0-1, floor/cap 0-5000).';
      case 404:
        return 'Video not found - reload the video list.';
      default:
        return err.message || `Reselect failed (HTTP ${err.status}).`;
    }
  }
  return err instanceof Error ? err.message : String(err);
}
