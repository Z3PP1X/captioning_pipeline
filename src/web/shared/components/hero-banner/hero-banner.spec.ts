import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HeroBanner } from './hero-banner';

describe('HeroBanner', () => {
  let fixture: ComponentFixture<HeroBanner>;
  let el: HTMLElement;

  function setInputs(inputs: Record<string, unknown>): void {
    for (const [key, value] of Object.entries(inputs)) {
      fixture.componentRef.setInput(key, value);
    }
    fixture.detectChanges();
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HeroBanner] }).compileComponents();
    fixture = TestBed.createComponent(HeroBanner);
    el = fixture.nativeElement as HTMLElement;
    setInputs({
      poster: { src: 'shared/mim26.png', alt: 'Plakat zum Kongress' },
      title: 'Was tun?! Kongress',
    });
  });

  it('renders the title as the only h1 inside the #kongress section', () => {
    const section = el.querySelector('section#kongress');
    expect(section).toBeTruthy();
    expect(section?.className).toContain('scroll-mt-14');
    const headings = el.querySelectorAll('h1');
    expect(headings.length).toBe(1);
    expect(headings[0].textContent?.trim()).toBe('Was tun?! Kongress');
  });

  it('renders the poster with src and alt inside an aspect-ratio container', () => {
    const img = el.querySelector<HTMLImageElement>('[data-slot="aspect-ratio"] img');
    expect(img).toBeTruthy();
    expect(img?.getAttribute('src')).toContain('shared/mim26.png');
    expect(img?.getAttribute('alt')).toBe('Plakat zum Kongress');
  });

  it('omits tagline and meta list when not provided', () => {
    expect(el.querySelector('p')).toBeNull();
    expect(el.querySelector('ul')).toBeNull();
  });

  it('renders tagline, date and location when provided', () => {
    setInputs({ tagline: 'Gemeinsam handeln.', dateLabel: 'Termin folgt', location: 'München' });
    expect(el.querySelector('p')?.textContent).toContain('Gemeinsam handeln.');
    const items = Array.from(el.querySelectorAll('li')).map((li) => li.textContent ?? '');
    expect(items.length).toBe(2);
    expect(items[0]).toContain('Termin folgt');
    expect(items[1]).toContain('München');
  });
});
