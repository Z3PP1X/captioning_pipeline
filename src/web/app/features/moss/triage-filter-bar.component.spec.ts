import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { TriageFilterBarComponent } from './triage-filter-bar.component';
import { TriageFilter } from '../../core/models/moss.model';

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
  }
}

describe('TriageFilterBarComponent', () => {
  let fixture: ComponentFixture<TriageFilterBarComponent>;
  let component: TriageFilterBarComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TriageFilterBarComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(TriageFilterBarComponent);
    component = fixture.componentInstance;
    await settle(fixture);
  });

  it('renders filter tabs with counts and updates active filter on click', async () => {
    fixture.componentRef.setInput('allCount', 50);
    fixture.componentRef.setInput('leakageCount', 4);
    fixture.componentRef.setInput('watermarkCount', 7);
    fixture.componentRef.setInput('fallbackCount', 2);
    fixture.componentRef.setInput('approvedCount', 37);
    await settle(fixture);

    expect(component.countFor('all')).toBe(50);
    expect(component.countFor('leakage')).toBe(4);
    expect(component.countFor('watermark')).toBe(7);

    let changed: TriageFilter | undefined;
    component.filterChange.subscribe((f) => (changed = f));

    component.selectFilter('leakage');
    await settle(fixture);

    expect(component.activeFilter()).toBe('leakage');
    expect(changed).toBe('leakage');
  });

  it('handles navigation and triage keyboard shortcuts J, K, Enter, R, C, Shift+C', async () => {
    let nextCount = 0;
    let prevCount = 0;
    let approveCount = 0;
    let fallbackCount = 0;
    let cropCount = 0;
    let cropAllCount = 0;

    component.navigateNext.subscribe(() => nextCount++);
    component.navigatePrevious.subscribe(() => prevCount++);
    component.approveAndAdvance.subscribe(() => approveCount++);
    component.regenerateFallback.subscribe(() => fallbackCount++);
    component.applyCrop.subscribe(() => cropCount++);
    component.applyCropToAll.subscribe(() => cropAllCount++);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'j' }));
    expect(nextCount).toBe(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' }));
    expect(prevCount).toBe(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(approveCount).toBe(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r' }));
    expect(fallbackCount).toBe(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', shiftKey: false }));
    expect(cropCount).toBe(1);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'C', shiftKey: true }));
    expect(cropAllCount).toBe(1);
  });
});
