import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { DisentanglementConfigComponent } from './disentanglement-config.component';
import { STANDARD_STYLE_BLACKLIST } from '../../core/models/moss.model';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

describe('DisentanglementConfigComponent', () => {
  let fixture: ComponentFixture<DisentanglementConfigComponent>;
  let component: DisentanglementConfigComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DisentanglementConfigComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(DisentanglementConfigComponent);
    component = fixture.componentInstance;
    await settle(fixture);
  });

  it('initializes with default MOSS-Image configuration', () => {
    expect(component.pipeline()).toBe('moss');
    expect(component.trainingMode()).toBe('CHARACTER');
    expect(component.classNoun()).toBe('woman');
    expect(component.triggerPhrase()).toBe('ohwx_subject');
    expect(component.lockedTraits()).toEqual([]);
  });

  it('toggles pipeline between krea2 and moss', async () => {
    component.setPipeline('krea2');
    await settle(fixture);
    expect(component.pipeline()).toBe('krea2');

    component.setPipeline('moss');
    await settle(fixture);
    expect(component.pipeline()).toBe('moss');
  });

  it('switches training mode cards', async () => {
    component.setTrainingMode('AESTHETIC_STYLE');
    await settle(fixture);
    expect(component.trainingMode()).toBe('AESTHETIC_STYLE');

    component.setTrainingMode('OBJECT_CONCEPT');
    await settle(fixture);
    expect(component.trainingMode()).toBe('OBJECT_CONCEPT');

    component.setTrainingMode('CHARACTER');
    await settle(fixture);
    expect(component.trainingMode()).toBe('CHARACTER');
  });

  it('adds and removes locked traits, preventing duplicates and empty strings', async () => {
    component.newTraitText.set('watermark');
    component.addTraitFromInput();
    await settle(fixture);
    expect(component.lockedTraits()).toEqual(['watermark']);
    expect(component.newTraitText()).toBe('');

    // Case-insensitive duplicate check
    component.newTraitText.set(' WATERMARK ');
    component.addTraitFromInput();
    await settle(fixture);
    expect(component.lockedTraits()).toEqual(['watermark']);

    // Empty string check
    component.newTraitText.set('   ');
    component.addTraitFromInput();
    await settle(fixture);
    expect(component.lockedTraits()).toEqual(['watermark']);

    // Add second trait
    component.addTrait('lineart');
    await settle(fixture);
    expect(component.lockedTraits()).toEqual(['watermark', 'lineart']);

    // Remove first trait
    component.removeTrait(0);
    await settle(fixture);
    expect(component.lockedTraits()).toEqual(['lineart']);
  });

  it('loads the standard style blacklist and clears all', async () => {
    component.loadStandardStyleBlacklist();
    await settle(fixture);
    expect(component.lockedTraits().length).toBe(STANDARD_STYLE_BLACKLIST.length);
    for (const item of STANDARD_STYLE_BLACKLIST) {
      expect(component.lockedTraits()).toContain(item.toLowerCase());
    }

    component.clearAllTraits();
    await settle(fixture);
    expect(component.lockedTraits()).toEqual([]);
  });

  it('updates class noun and trigger phrase', async () => {
    component.onClassNounChange('supercar');
    component.onTriggerPhraseChange('tok_speed');
    await settle(fixture);

    expect(component.classNoun()).toBe('supercar');
    expect(component.triggerPhrase()).toBe('tok_speed');
    expect(component.currentConfig()).toEqual({
      pipeline: 'moss',
      trainingMode: 'CHARACTER',
      lockedTraits: [],
      classNoun: 'supercar',
      triggerPhrase: 'tok_speed',
    });
  });
});
