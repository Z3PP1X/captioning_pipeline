import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FocusTopic } from '../../../core/models/congress.model';
import { FOCUS_TOPIC_TITLE_ID, FocusTopicsSection } from './focus-topics-section';

const TOPICS: FocusTopic[] = [
  { id: 'a', title: 'Thema A', teaser: 'Kurz A.', description: ['Lang A1.', 'Lang A2.'] },
  { id: 'b', title: 'Thema B', teaser: 'Kurz B.', description: ['Lang B1.'] },
];

describe('FocusTopicsSection', () => {
  let fixture: ComponentFixture<FocusTopicsSection>;
  let el: HTMLElement;

  const dialog = () => document.querySelector<HTMLElement>('[data-testid="focus-topic-dialog"]');
  const cards = () => Array.from(el.querySelectorAll<HTMLButtonElement>('button[data-slot="card"]'));

  async function settle(): Promise<void> {
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await fixture.whenStable();
  }

  beforeEach(async () => {
    // jsdom lacks matchMedia (CDK BreakpointObserver).
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }),
    });
    await TestBed.configureTestingModule({ imports: [FocusTopicsSection] }).compileComponents();
    fixture = TestBed.createComponent(FocusTopicsSection);
    el = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('title', 'Schwerpunkte');
    fixture.componentRef.setInput('intro', 'Vier Themen.');
    fixture.componentRef.setInput('topics', TOPICS);
    await settle();
  });

  it('renders the #schwerpunkte section with h2, intro and one carousel card per topic', () => {
    expect(el.querySelector('section#schwerpunkte')).toBeTruthy();
    expect(el.querySelector('h2')?.textContent?.trim()).toBe('Schwerpunkte');
    expect(el.querySelector('p')?.textContent).toContain('Vier Themen.');
    expect(el.querySelector('hlm-carousel')).toBeTruthy();
    expect(el.querySelectorAll('hlm-carousel-item').length).toBe(2);
    expect(cards()[0].textContent).toContain('Thema A');
    expect(cards()[0].textContent).toContain('Kurz A.');
    // Detail text only lives in the modal.
    expect(el.textContent).not.toContain('Lang A1.');
  });

  it('opens a dialog with the full description of the clicked topic, labelled by its title', async () => {
    cards()[1].click();
    await settle();

    const content = dialog();
    expect(content).not.toBeNull();
    expect(content!.closest('[role="dialog"]')?.getAttribute('aria-labelledby')).toBe(
      FOCUS_TOPIC_TITLE_ID,
    );
    expect(document.getElementById(FOCUS_TOPIC_TITLE_ID)?.textContent?.trim()).toBe('Thema B');
    expect(content!.textContent).toContain('Lang B1.');

    content!.querySelector<HTMLButtonElement>('button[data-slot="dialog-close"]')!.click();
    await settle();
    await vi.waitFor(() => expect(dialog()).toBeNull());
  });

  it('renders nothing when there are no topics', async () => {
    fixture.componentRef.setInput('topics', []);
    await settle();
    expect(el.querySelector('section')).toBeNull();
  });
});
