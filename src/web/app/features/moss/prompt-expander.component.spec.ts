import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { PromptExpanderComponent } from './prompt-expander.component';
import { MossService } from '../../core/services/moss.service';
import { PromptExpandResponse } from '../../core/models/moss.model';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

const MOCK_EXPAND_RESPONSE: PromptExpandResponse = {
  long: 'An atmospheric full-body portrait of a 25-year-old woman sitting on a rain-slicked wooden bench at a neon-lit bus stop at 2 AM. Soft reflections of cyan and magenta signage shimmer across wet asphalt puddles. High contrast chiaroscuro lighting, subtle rain droplets clinging to her wool coat, shot on 85mm prime lens f/1.8 with gentle optical bokeh.',
  medium: 'A young woman sitting quietly at a bus stop under night rain, illuminated by reflections of magenta and cyan neon lights on wet street asphalt.',
  short: 'ohwx_subject woman sitting at neon bus stop in rain.',
  exemplars: [
    {
      id: 101,
      stem: 'rainy_bus_stop_01',
      score: 0.92,
      matched_tags: ['rain', 'neon', 'bench', 'night'],
      full_caption: 'A woman in a heavy coat waiting at a drenched bus shelter under neon signage.',
    },
  ],
};

describe('PromptExpanderComponent', () => {
  let fixture: ComponentFixture<PromptExpanderComponent>;
  let component: PromptExpanderComponent;
  let mossServiceMock: { expandPrompt: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    mossServiceMock = {
      expandPrompt: vi.fn().mockResolvedValue(MOCK_EXPAND_RESPONSE),
    };

    await TestBed.configureTestingModule({
      imports: [PromptExpanderComponent],
      providers: [{ provide: MossService, useValue: mossServiceMock }],
    }).compileComponents();

    fixture = TestBed.createComponent(PromptExpanderComponent);
    component = fixture.componentInstance;
    await settle(fixture);
  });

  it('renders initial state with empty inputs', () => {
    expect(component.userIdea()).toBe('');
    expect(component.isExpanding()).toBe(false);
    expect(component.expandedResult()).toBeNull();
    expect(component.errorMessage()).toBeNull();
  });

  it('calls MossService.expandPrompt and displays 3-tier results with exemplar cards', async () => {
    component.userIdea.set('sitzt nachts im Regen an einer Bushaltestelle mit Neonreklame');
    await settle(fixture);

    await component.expandPrompt();
    await settle(fixture);

    expect(mossServiceMock.expandPrompt).toHaveBeenCalledWith(
      'sitzt nachts im Regen an einer Bushaltestelle mit Neonreklame',
      expect.objectContaining({
        trigger_word: 'ohwx_subject',
        class_noun: 'woman',
      }),
    );

    expect(component.expandedResult()).toEqual(MOCK_EXPAND_RESPONSE);
    expect(component.longWordCount()).toBeGreaterThan(30);
    expect(component.mediumWordCount()).toBeGreaterThan(15);
    expect(component.shortWordCount()).toBe(9);

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.textContent).toContain('rainy_bus_stop_01');
    expect(compiled.textContent).toContain('Score: 92%');
    expect(compiled.textContent).toContain('Tier 1: LONG');
  });

  it('handles error in expandPrompt gracefully', async () => {
    mossServiceMock.expandPrompt.mockRejectedValueOnce(new Error('Network connection timeout'));

    component.userIdea.set('eine szene');
    await component.expandPrompt();
    await settle(fixture);

    expect(component.errorMessage()).toContain('Network connection timeout');
    expect(component.expandedResult()).toBeNull();
  });

  it('copies generated tier prompt text with transient visual feedback', async () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextSpy,
      },
    });

    component.expandedResult.set(MOCK_EXPAND_RESPONSE);
    await settle(fixture);

    await component.copyText(MOCK_EXPAND_RESPONSE.long, 'long');
    expect(writeTextSpy).toHaveBeenCalledWith(MOCK_EXPAND_RESPONSE.long);
    expect(component.copiedTier()).toBe('long');
  });
});
