import { ComponentFixture, TestBed } from '@angular/core/testing';
import { lucideTicket } from '@ng-icons/lucide';
import { CtaSection } from './cta-section';

describe('CtaSection', () => {
  let fixture: ComponentFixture<CtaSection>;
  let el: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [CtaSection] }).compileComponents();
    fixture = TestBed.createComponent(CtaSection);
    el = fixture.nativeElement as HTMLElement;
    fixture.componentRef.setInput('title', 'Bleib informiert');
    fixture.componentRef.setInput('text', 'Wir sagen dir Bescheid.');
    fixture.componentRef.setInput('buttonLabel', 'Informiert bleiben');
    fixture.detectChanges();
  });

  it('renders title, text and button label in the #informiert-bleiben section', () => {
    expect(el.querySelector('section#informiert-bleiben')).toBeTruthy();
    expect(el.querySelector('h2')?.textContent?.trim()).toBe('Bleib informiert');
    expect(el.querySelector('p')?.textContent).toContain('Wir sagen dir Bescheid.');
    const button = el.querySelector('button');
    expect(button?.textContent?.trim()).toBe('Informiert bleiben');
    expect(button?.getAttribute('data-slot')).toBe('button');
  });

  it('emits action on button click', () => {
    const spy = vi.fn();
    fixture.componentInstance.action.subscribe(spy);
    el.querySelector('button')?.click();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('shows the default icon and hides it with icon = null', () => {
    expect(el.querySelector('button ng-icon')).toBeTruthy();
    fixture.componentRef.setInput('icon', null);
    fixture.detectChanges();
    expect(el.querySelector('button ng-icon')).toBeNull();
  });

  it('can be reused as a different CTA (custom id and icon)', () => {
    fixture.componentRef.setInput('sectionId', 'tickets');
    fixture.componentRef.setInput('icon', lucideTicket);
    fixture.detectChanges();
    expect(el.querySelector('section#tickets')).toBeTruthy();
    expect(el.querySelector('h2')?.id).toBe('tickets-title');
    expect(el.querySelector('button ng-icon')).toBeTruthy();
  });
});
