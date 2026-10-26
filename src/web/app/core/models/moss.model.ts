export type PipelineType = 'krea2' | 'moss';

export type TrainingMode = 'CHARACTER' | 'AESTHETIC_STYLE' | 'OBJECT_CONCEPT';

export interface TrainingModeInfo {
  mode: TrainingMode;
  label: string;
  badge: string;
  icon: string;
  description: string;
}

export const TRAINING_MODES: TrainingModeInfo[] = [
  {
    mode: 'CHARACTER',
    label: 'Character',
    badge: 'Subjekt-Fokus',
    icon: 'lucideUser',
    description: 'Entkoppelt Subjekt- und Gesichtsmerkmale von Kleidung, Hintergrund, Beleuchtung und Stil.',
  },
  {
    mode: 'AESTHETIC_STYLE',
    label: 'Aesthetic Style',
    badge: 'Stil-Fokus',
    icon: 'lucidePalette',
    description: 'Erfasst Malstil, Pinselstrich, Farbpalette und visuelles Medium unter Abstraktion des Bildinhalts.',
  },
  {
    mode: 'OBJECT_CONCEPT',
    label: 'Object / Concept',
    badge: 'Objekt-Fokus',
    icon: 'lucidePackage',
    description: 'Fokussiert auf Objektgeometrie, taktile Materialeigenschaften und mechanische Struktur.',
  },
];

export const STANDARD_STYLE_BLACKLIST: readonly string[] = [
  'painting',
  'illustration',
  'drawing',
  'sketch',
  '3d render',
  'cgi',
  'anime',
  'manga',
  'watercolor',
  'oil on canvas',
  'impasto',
  'brushstrokes',
  'lineart',
  'cel-shaded',
  'pastel',
  'color palette',
  'artstyle',
];

export interface DisentanglementConfig {
  pipeline: PipelineType;
  trainingMode: TrainingMode;
  lockedTraits: string[];
  classNoun: string;
  triggerPhrase: string;
}

export interface ThreeTierCaption {
  long: string;
  medium: string;
  short: string;
}

export interface TierTargetRange {
  min: number;
  max: number;
}

export const TIER_RANGES: Record<'long' | 'medium' | 'short', TierTargetRange> = {
  long: { min: 110, max: 220 },
  medium: { min: 40, max: 75 },
  short: { min: 10, max: 22 },
};

export interface WatermarkBox {
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string;
}

export interface SmartCropBox {
  x: number;
  y: number;
  width: number;
  height: number;
  targetBucket?: string;
}

export type BorderDirection = 'up' | 'down' | 'left' | 'right';

export type TriageFilter = 'all' | 'leakage' | 'watermark' | 'fallback' | 'approved';

export interface PromptExemplar {
  id?: number | string;
  stem?: string;
  score?: number;
  matched_tags?: string[];
  img_rel_path?: string | null;
  full_caption?: string;
}

export interface PromptExpandResponse {
  long: string;
  medium: string;
  short: string;
  exemplars: PromptExemplar[];
}
