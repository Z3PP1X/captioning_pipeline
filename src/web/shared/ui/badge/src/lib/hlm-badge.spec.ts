import { ChangeDetectionStrategy, Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, it, expect } from 'vitest';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';

@Component({
  imports: [HlmBadgeImports],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span hlmBadge variant="info" data-testid="info">Curating</span>
    <span hlmBadge variant="success" data-testid="success">Exported</span>
    <span hlmBadge variant="warning" data-testid="warning">Extracting</span>
  `,
})
class BadgeHost {}

describe('HlmBadge semantic variants (decision 4)', () => {
  const render = () => {
    const fixture = TestBed.createComponent(BadgeHost);
    fixture.detectChanges();
    return (id: string) => fixture.nativeElement.querySelector(`[data-testid="${id}"]`) as HTMLElement;
  };

  it('has an info variant with ink text on the info tint', () => {
    const el = render()('info');
    expect(el.getAttribute('data-variant')).toBe('info');
    expect(el.className).toContain('bg-info/10');
    expect(el.className).toContain('text-foreground');
  });

  it('colours success / warning icons with the AA *-strong tokens, text stays ink', () => {
    const q = render();
    expect(q('success').className).toContain('[&>ng-icon]:text-success-strong');
    expect(q('warning').className).toContain('[&>ng-icon]:text-warning-strong');
    expect(q('success').className).toContain('text-foreground');
    expect(q('warning').className).toContain('text-foreground');
  });
});
