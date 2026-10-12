import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FaqItem } from '../../../core/models/faq.model';
import { FaqSection } from './faq-section';

const ITEMS: FaqItem[] = [
  { id: 'a', question: 'Frage A?', answer: 'Antwort A.' },
  { id: 'b', question: 'Frage B?', answer: 'Antwort B.' },
];

describe('FaqSection', () => {
  let fixture: ComponentFixture<FaqSection>;
  let el: HTMLElement;

  function triggers(): HTMLButtonElement[] {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('[data-slot="accordion-trigger"]'));
  }

  function contents(): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>('hlm-accordion-content'));
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [FaqSection] }).compileComponents();
    fixture = TestBed.createComponent(FaqSection);
    el = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('title', 'Häufige Fragen');
    fixture.componentRef.setInput('items', ITEMS);
    fixture.detectChanges();
  });

  it('renders the #faq section with h2 and one accordion item per FAQ entry', () => {
    expect(el.querySelector('section#faq')).toBeTruthy();
    expect(el.querySelector('h2')?.textContent?.trim()).toBe('Häufige Fragen');
    expect(triggers().map((t) => t.textContent?.trim())).toEqual(['Frage A?', 'Frage B?']);
    expect(contents()[1].textContent).toContain('Antwort B.');
  });

  it('starts collapsed', () => {
    expect(triggers().every((t) => t.getAttribute('aria-expanded') === 'false')).toBe(true);
    expect(contents().every((c) => c.getAttribute('data-state') === 'closed')).toBe(true);
  });

  it('opens one item at a time and collapses it again (single, collapsible)', () => {
    const [a, b] = triggers();

    a.click();
    fixture.detectChanges();
    expect(a.getAttribute('aria-expanded')).toBe('true');
    expect(contents()[0].getAttribute('data-state')).toBe('open');

    b.click();
    fixture.detectChanges();
    expect(a.getAttribute('aria-expanded')).toBe('false');
    expect(b.getAttribute('aria-expanded')).toBe('true');

    b.click();
    fixture.detectChanges();
    expect(triggers().every((t) => t.getAttribute('aria-expanded') === 'false')).toBe(true);
  });

  it('renders nothing when there are no items', () => {
    fixture.componentRef.setInput('items', []);
    fixture.detectChanges();
    expect(el.querySelector('section')).toBeNull();
  });
});
