export function formatTime(s: number | null | undefined): string {
  if (s === null || s === undefined || isNaN(s)) return '00:00:00.00';
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  const ms = Math.floor((s % 1) * 100);
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
}

export function formatShortTime(s: number | null | undefined): string {
  if (s === null || s === undefined || isNaN(s)) return '--:--';
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  if (hrs > 0) {
    return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function formatHms(s: number | null | undefined): string {
  if (s === null || s === undefined || isNaN(s)) return '00:00:00';
  const total = Math.max(0, Math.round(s));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export function parseTimestamp(str: string | null | undefined): number | null {
  if (!str) return null;
  const trimmed = str.trim();
  if (!trimmed) return null;

  // e.g. "90s", "90", "12.5"
  if (/^\d+(\.\d+)?s?$/i.test(trimmed)) {
    return Math.max(0, parseFloat(trimmed.replace(/s$/i, '')));
  }

  // e.g. MM:SS, HH:MM:SS, or with decimals
  const parts = trimmed.split(':');
  if (parts.length > 3) return null;
  const numParts = parts.map((p) => parseFloat(p));
  if (numParts.some(isNaN)) return null;

  if (numParts.length === 3) {
    return Math.max(0, numParts[0] * 3600 + numParts[1] * 60 + numParts[2]);
  } else if (numParts.length === 2) {
    return Math.max(0, numParts[0] * 60 + numParts[1]);
  } else if (numParts.length === 1) {
    return Math.max(0, numParts[0]);
  }
  return null;
}
