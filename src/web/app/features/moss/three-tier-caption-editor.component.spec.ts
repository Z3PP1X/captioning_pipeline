import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  ThreeTierCaptionEditorComponent,
  countWords,
  detectTraitLeaks,
  extractQuotes,
  removeTraitFromText,
} from './three-tier-caption-editor.component';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

function generateWords(count: number, prefix = 'word'): string {
  return Array.from({ length: count }, (_, i) => `${prefix}${i + 1}`).join(' ');
}

describe('ThreeTierCaptionEditorComponent helper functions', () => {
  it('correctly counts words across varied whitespace', () => {
    expect(countWords('')).toBe(0);
    expect(countWords('   ')).toBe(0);
    expect(countWords('hello world')).toBe(2);
    expect(countWords('  this   is a    spaced   sentence  ')).toBe(5);
  });

  it('extracts double-quoted verbatim phrases', () => {
    expect(extractQuotes('Plain text without quotes')).toEqual([]);
    expect(extractQuotes('A sign saying "Coffee Shop" and a t-shirt saying "NO SIGNAL" in bold.')).toEqual([
      'Coffee Shop',
      'NO SIGNAL',
    ]);
  });

  it('detects trait leaks with whole word boundaries case-insensitively', () => {
    const traits = ['anime', 'oil on canvas', 'sketch'];
    expect(detectTraitLeaks('A modern portrait in Anime style.', traits)).toEqual(['anime']);
    expect(detectTraitLeaks('An animated movie scene.', traits)).toEqual([]);
    expect(detectTraitLeaks('Classic oil on canvas technique with a sketch background.', traits)).toEqual([
      'oil on canvas',
      'sketch',
    ]);
  });

  it('removes trait cleanly from text without leaving double commas or excess spaces', () => {
    const original = 'A photo of a woman, anime, standing in a garden, sketch, sunny day';
    const cleaned = removeTraitFromText(original, 'anime');
    expect(cleaned).not.toContain('anime');
    expect(cleaned).toContain('sketch');

    const cleanAll = removeTraitFromText(cleaned, 'sketch');
    expect(cleanAll).not.toContain('sketch');
    expect(cleanAll).toBe('A photo of a woman, standing in a garden, sunny day');
  });
});

describe('ThreeTierCaptionEditorComponent', () => {
  let fixture: ComponentFixture<ThreeTierCaptionEditorComponent>;
  let component: ThreeTierCaptionEditorComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ThreeTierCaptionEditorComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(ThreeTierCaptionEditorComponent);
    component = fixture.componentInstance;
    await settle(fixture);
  });

  it('evaluates hierarchy validity long > medium > short >= 5', async () => {
    // Initially empty -> short is 0 < 5 -> invalid
    expect(component.isHierarchyValid()).toBe(false);

    // Short: 12 words, Medium: 45 words, Long: 125 words -> valid
    component.shortCaption.set(generateWords(12));
    component.mediumCaption.set(generateWords(45));
    component.longCaption.set(generateWords(125));
    await settle(fixture);

    expect(component.isHierarchyValid()).toBe(true);
    expect(component.isShortValid()).toBe(true);
    expect(component.isMediumValid()).toBe(true);
    expect(component.isLongValid()).toBe(true);

    // If Short < 5 -> invalid
    component.shortCaption.set('one two three');
    await settle(fixture);
    expect(component.isHierarchyValid()).toBe(false);

    // If Medium <= Short -> invalid
    component.shortCaption.set(generateWords(12));
    component.mediumCaption.set(generateWords(10));
    await settle(fixture);
    expect(component.isHierarchyValid()).toBe(false);

    // If Long <= Medium -> invalid
    component.mediumCaption.set(generateWords(50));
    component.longCaption.set(generateWords(40));
    await settle(fixture);
    expect(component.isHierarchyValid()).toBe(false);
  });

  it('identifies leaked traits from lockedTraits across tiers', async () => {
    fixture.componentRef.setInput('lockedTraits', ['painting', 'manga', 'watermark']);
    component.longCaption.set('A beautiful painting of a cityscape at dusk.');
    component.mediumCaption.set('A cityscape with a watermark in the corner.');
    component.shortCaption.set('Cityscape in manga format.');
    await settle(fixture);

    expect(component.longLeaks()).toEqual(['painting']);
    expect(component.mediumLeaks()).toEqual(['watermark']);
    expect(component.shortLeaks()).toEqual(['manga']);
    expect(component.allLeakedTraits()).toContain('painting');
    expect(component.allLeakedTraits()).toContain('watermark');
    expect(component.allLeakedTraits()).toContain('manga');
  });

  it('strips a single leaked trait from all three tiers on click', async () => {
    fixture.componentRef.setInput('lockedTraits', ['anime']);
    component.longCaption.set('A woman in anime style standing outside in rainy weather.');
    component.mediumCaption.set('A woman with anime eyes standing outside.');
    component.shortCaption.set('A woman, anime.');
    await settle(fixture);

    expect(component.allLeakedTraits()).toContain('anime');

    component.stripTrait('anime');
    await settle(fixture);

    expect(component.longCaption()).not.toContain('anime');
    expect(component.mediumCaption()).not.toContain('anime');
    expect(component.shortCaption()).not.toContain('anime');
    expect(component.allLeakedTraits()).toEqual([]);
  });

  it('strips all detected leaked traits at once', async () => {
    fixture.componentRef.setInput('lockedTraits', ['sketch', 'watercolor', 'cgi']);
    component.longCaption.set('A sketch of a tree with watercolor accents.');
    component.mediumCaption.set('Tree with cgi elements.');
    component.shortCaption.set('A sketch tree.');
    await settle(fixture);

    expect(component.allLeakedTraits().length).toBe(3);

    component.stripAllLeakedTraits();
    await settle(fixture);

    expect(component.allLeakedTraits()).toEqual([]);
    expect(component.longCaption()).not.toContain('sketch');
    expect(component.longCaption()).not.toContain('watercolor');
    expect(component.mediumCaption()).not.toContain('cgi');
    expect(component.shortCaption()).not.toContain('sketch');
  });

  it('extracts verbatim quotes for cyan highlighting', async () => {
    component.longCaption.set('A street with a cafe neon sign reading "HOT COFFEE" and a banner "OPEN 24H".');
    await settle(fixture);

    expect(component.longQuotes()).toEqual(['HOT COFFEE', 'OPEN 24H']);
  });

  it('copies text to clipboard and displays transient copied status', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextSpy,
      },
    });

    await component.copyToClipboard('Test Caption', 'long');
    expect(writeTextSpy).toHaveBeenCalledWith('Test Caption');
    expect(component.copiedKey()).toBe('long');
  });
});
