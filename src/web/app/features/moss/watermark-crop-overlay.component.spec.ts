import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  WatermarkCropOverlayComponent,
  normalizeToSvg,
} from './watermark-crop-overlay.component';
import { SmartCropBox, WatermarkBox } from '../../core/models/moss.model';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

describe('normalizeToSvg helper', () => {
  it('scales normalized [0, 1] coordinates to SVG view dimensions', () => {
    const box = { x: 0.1, y: 0.2, width: 0.8, height: 0.6 };
    const scaled = normalizeToSvg(box, 1920, 1080);
    expect(scaled.x).toBe(192);
    expect(scaled.y).toBe(216);
    expect(scaled.width).toBe(1536);
    expect(scaled.height).toBe(648);
  });

  it('preserves absolute pixel coordinates when already in pixel space', () => {
    const box = { x: 100, y: 150, width: 800, height: 600 };
    const result = normalizeToSvg(box, 1920, 1080);
    expect(result).toEqual(box);
  });
});

describe('WatermarkCropOverlayComponent', () => {
  let fixture: ComponentFixture<WatermarkCropOverlayComponent>;
  let component: WatermarkCropOverlayComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WatermarkCropOverlayComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(WatermarkCropOverlayComponent);
    component = fixture.componentInstance;
    await settle(fixture);
  });

  it('renders SVG watermark boxes and smart crop with target bucket', async () => {
    const watermarks: WatermarkBox[] = [
      { x: 100, y: 100, width: 250, height: 80, label: 'Shutterstock' },
    ];
    const smartCrop: SmartCropBox = {
      x: 50,
      y: 50,
      width: 1152,
      height: 896,
      targetBucket: '1152×896',
    };

    fixture.componentRef.setInput('imageWidth', 1920);
    fixture.componentRef.setInput('imageHeight', 1080);
    fixture.componentRef.setInput('watermarks', watermarks);
    fixture.componentRef.setInput('smartCrop', smartCrop);
    fixture.componentRef.setInput('borders', ['up', 'down']);
    await settle(fixture);

    expect(component.svgWatermarks().length).toBe(1);
    expect(component.svgWatermarks()[0].label).toBe('Shutterstock');
    expect(component.svgSmartCrop()?.targetBucket).toBe('1152×896');

    const compiled = fixture.nativeElement as HTMLElement;
    const watermarkGroup = compiled.querySelector('.watermark-group');
    expect(watermarkGroup).not.toBeNull();
    expect(watermarkGroup?.textContent).toContain('Shutterstock');

    const smartCropGroup = compiled.querySelector('.smart-crop-group');
    expect(smartCropGroup).not.toBeNull();
    expect(smartCropGroup?.textContent).toContain('1152×896');
  });

  it('emits applyCrop on button click', async () => {
    const smartCrop: SmartCropBox = {
      x: 100,
      y: 100,
      width: 800,
      height: 600,
      targetBucket: '800×600',
    };
    fixture.componentRef.setInput('smartCrop', smartCrop);
    await settle(fixture);

    let emitted: SmartCropBox | undefined;
    component.applyCrop.subscribe((c) => (emitted = c));

    component.onApplyCrop();
    expect(emitted).toEqual(smartCrop);
  });

  it('emits applyCropToAll on button click', async () => {
    const smartCrop: SmartCropBox = {
      x: 100,
      y: 100,
      width: 800,
      height: 600,
      targetBucket: '800×600',
    };
    fixture.componentRef.setInput('smartCrop', smartCrop);
    await settle(fixture);

    let emittedAll: SmartCropBox | undefined;
    component.applyCropToAll.subscribe((c) => (emittedAll = c));

    component.onApplyCropToAll();
    expect(emittedAll).toEqual(smartCrop);
  });

  it('handles keyboard shortcuts C and Shift+C', async () => {
    const smartCrop: SmartCropBox = {
      x: 0,
      y: 0,
      width: 512,
      height: 512,
      targetBucket: '512×512',
    };
    fixture.componentRef.setInput('smartCrop', smartCrop);
    await settle(fixture);

    let cropCount = 0;
    let cropAllCount = 0;
    component.applyCrop.subscribe(() => cropCount++);
    component.applyCropToAll.subscribe(() => cropAllCount++);

    // Press C
    const eventC = new KeyboardEvent('keydown', { key: 'c', shiftKey: false });
    window.dispatchEvent(eventC);
    expect(cropCount).toBe(1);
    expect(cropAllCount).toBe(0);

    // Press Shift+C
    const eventShiftC = new KeyboardEvent('keydown', { key: 'C', shiftKey: true });
    window.dispatchEvent(eventShiftC);
    expect(cropCount).toBe(1);
    expect(cropAllCount).toBe(1);
  });
});
