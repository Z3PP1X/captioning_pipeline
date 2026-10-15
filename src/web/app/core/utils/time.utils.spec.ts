import { describe, it, expect } from 'vitest';
import { formatTime, formatShortTime, formatHms, parseTimestamp } from './time.utils';

describe('time.utils', () => {
  describe('formatTime', () => {
    it('handles null, undefined and NaN', () => {
      expect(formatTime(null)).toBe('00:00:00.00');
      expect(formatTime(undefined)).toBe('00:00:00.00');
      expect(formatTime(NaN)).toBe('00:00:00.00');
    });

    it('formats seconds with fractions into HH:MM:SS.ms', () => {
      expect(formatTime(0)).toBe('00:00:00.00');
      expect(formatTime(65.5)).toBe('00:01:05.50');
      expect(formatTime(3665.25)).toBe('01:01:05.25');
    });
  });

  describe('formatShortTime', () => {
    it('returns --:-- for invalid inputs', () => {
      expect(formatShortTime(null)).toBe('--:--');
      expect(formatShortTime(undefined)).toBe('--:--');
      expect(formatShortTime(NaN)).toBe('--:--');
    });

    it('formats MM:SS when less than an hour', () => {
      expect(formatShortTime(0)).toBe('00:00');
      expect(formatShortTime(75)).toBe('01:15');
      expect(formatShortTime(599)).toBe('09:59');
    });

    it('formats HH:MM:SS when an hour or greater', () => {
      expect(formatShortTime(3600)).toBe('01:00:00');
      expect(formatShortTime(3665)).toBe('01:01:05');
    });
  });

  describe('formatHms', () => {
    it('handles null and undefined', () => {
      expect(formatHms(null)).toBe('00:00:00');
      expect(formatHms(undefined)).toBe('00:00:00');
    });

    it('formats total seconds into HH:MM:SS', () => {
      expect(formatHms(45)).toBe('00:00:45');
      expect(formatHms(725)).toBe('00:12:05');
      expect(formatHms(3600)).toBe('01:00:00');
    });
  });

  describe('parseTimestamp', () => {
    it('returns null for empty or invalid string', () => {
      expect(parseTimestamp('')).toBeNull();
      expect(parseTimestamp('   ')).toBeNull();
      expect(parseTimestamp('abc')).toBeNull();
      expect(parseTimestamp('1:2:3:4')).toBeNull();
    });

    it('parses raw seconds with or without s suffix', () => {
      expect(parseTimestamp('45')).toBe(45);
      expect(parseTimestamp('12.5s')).toBe(12.5);
      expect(parseTimestamp('90s')).toBe(90);
    });

    it('parses MM:SS format', () => {
      expect(parseTimestamp('01:30')).toBe(90);
      expect(parseTimestamp('10:00')).toBe(600);
      expect(parseTimestamp('0:15.5')).toBe(15.5);
    });

    it('parses HH:MM:SS format', () => {
      expect(parseTimestamp('01:00:00')).toBe(3600);
      expect(parseTimestamp('01:02:03')).toBe(3723);
    });
  });
});
