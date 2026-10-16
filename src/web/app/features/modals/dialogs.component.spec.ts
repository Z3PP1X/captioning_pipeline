import { Injectable } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { InteractivityChecker } from '@angular/cdk/a11y';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ApiService } from '../../core/services/api.service';
import { UiStateService } from '../../core/services/ui-state.service';
import { CurateService } from '../../core/services/curate.service';
import { CandidateFrame } from '../../core/models/candidate.model';
import { PersonModalComponent } from './person-modal.component';
import { TagManagerModalComponent } from './tag-manager-modal.component';
import { LightboxModalComponent } from './lightbox-modal.component';

/** Detects changes until the CDK dialog / afterNextRender work has settled (zoneless TestBed). */
async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 4; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

const api = () => ({
  get: vi.fn().mockResolvedValue({ persons: [], tags: [], categories: [], regions: [], summary: [] }),
  post: vi.fn().mockResolvedValue({}),
  put: vi.fn().mockResolvedValue({}),
  patch: vi.fn().mockResolvedValue({}),
  delete: vi.fn().mockResolvedValue({}),
});

describe('hlm-dialog modals mounted behind @if (person, tag library)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [{ provide: ApiService, useValue: api() }] });
  });

  for (const [name, type, title] of [
    ['PersonModalComponent', PersonModalComponent, 'Person & Character Management'],
    ['TagManagerModalComponent', TagManagerModalComponent, 'Tag library'],
  ] as const) {
    describe(name, () => {
      let fixture: ComponentFixture<PersonModalComponent | TagManagerModalComponent>;
      let closed: number;

      beforeEach(async () => {
        fixture = TestBed.createComponent<PersonModalComponent | TagManagerModalComponent>(type);
        closed = 0;
        fixture.componentInstance.close.subscribe(() => closed++);
        await settle(fixture);
      });

      afterEach(() => fixture.destroy());

      it('opens as a labelled modal dialog in the CDK overlay and blocks global shortcuts', () => {
        const d = document.querySelector<HTMLElement>('.cdk-overlay-container [role="dialog"]')!;
        expect(d).not.toBeNull();
        expect(d.getAttribute('aria-modal')).toBe('true');
        expect(document.getElementById(d.getAttribute('aria-labelledby')!)?.textContent?.trim()).toBe(title);
        expect(TestBed.inject(UiStateService).modalOpen()).toBe(true);
        // Focus moved into the dialog
        expect(d.contains(document.activeElement)).toBe(true);
      });

      it('emits close from the built-in close button', async () => {
        document.querySelector<HTMLButtonElement>('[data-slot="dialog-close"]')!.click();
        await settle(fixture);
        expect(closed).toBe(1);
      });

      it('emits close on a backdrop click', async () => {
        document.querySelector<HTMLElement>('.cdk-overlay-backdrop')!.click();
        await settle(fixture);
        expect(closed).toBe(1);
      });

      it('unregisters from UiStateService and removes the overlay when the shell drops it', async () => {
        fixture.destroy();
        expect(TestBed.inject(UiStateService).modalOpen()).toBe(false);
        expect(document.querySelector('.cdk-overlay-container [role="dialog"]')).toBeNull();
      });
    });
  }
});

/** jsdom has no layout, so CDK sees every element as invisible (hence unfocusable); treat them as visible. */
@Injectable()
class LayoutFreeInteractivityChecker extends InteractivityChecker {
  override isVisible(): boolean {
    return true;
  }
}

describe('LightboxModalComponent (custom media surface)', () => {
  let fixture: ComponentFixture<LightboxModalComponent>;
  let curate: CurateService;
  let opener: HTMLButtonElement;

  const frame = (id: number): CandidateFrame =>
    ({ id, video_id: 'v1', chunk_id: 1, time_s: id, is_pick: true, state: null }) as CandidateFrame;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        { provide: ApiService, useValue: api() },
        { provide: InteractivityChecker, useClass: LayoutFreeInteractivityChecker },
      ],
    });
    curate = TestBed.inject(CurateService);
    curate.candidates.set([frame(1), frame(2)]);
    fixture = TestBed.createComponent(LightboxModalComponent);
    opener = document.createElement('button');
    opener.textContent = 'Open frame';
    document.body.appendChild(opener);
    await settle(fixture);
  });

  afterEach(() => {
    fixture.destroy();
    opener.remove();
  });

  it('keeps role=dialog/aria-modal, moves focus inside and restores it to the opener on close', async () => {
    opener.focus();
    curate.openLightbox(1);
    await settle(fixture);

    const panel = fixture.nativeElement.querySelector('[role="dialog"]') as HTMLElement;
    expect(panel).not.toBeNull();
    expect(panel.getAttribute('aria-modal')).toBe('true');
    expect(panel.contains(document.activeElement)).toBe(true);

    // Navigating keeps the trap (and focus) inside the same dialog
    curate.nextLightbox();
    await settle(fixture);
    expect(panel.contains(document.activeElement)).toBe(true);

    curate.closeLightbox();
    await settle(fixture);
    expect(fixture.nativeElement.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
