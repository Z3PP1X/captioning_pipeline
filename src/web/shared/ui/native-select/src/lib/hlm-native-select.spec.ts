import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';

interface Opt {
  id: string;
  name: string;
}

/** Options arrive after the bound value, like the caption-model list loaded from `/api/openrouter/models`. */
@Component({
  imports: [HlmNativeSelectImports],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-native-select selectId="late" [value]="value()" (valueChange)="value.set($event ?? '')">
      @for (o of options(); track o.id) {
        <option [value]="o.id">{{ o.name }}</option>
      }
    </hlm-native-select>
  `,
})
class LateOptionsHost {
  readonly value = signal('model-c');
  readonly options = signal<Opt[]>([]);
}

let fixture: ComponentFixture<LateOptionsHost>;
const flush = async () => {
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    // MutationObserver callbacks run as microtasks
    await Promise.resolve();
  }
};
const select = () => fixture.nativeElement.querySelector('select#late') as HTMLSelectElement;

describe('HlmNativeSelect with async-loaded options', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [LateOptionsHost] });
    fixture = TestBed.createComponent(LateOptionsHost);
  });

  it('shows the bound value once the options render after the value (no [selected] needed)', async () => {
    await flush();
    expect(select().options.length).toBe(0);

    fixture.componentInstance.options.set([
      { id: 'model-a', name: 'A' },
      { id: 'model-b', name: 'B' },
      { id: 'model-c', name: 'C' },
    ]);
    await flush();

    // value and the visible option both follow the model
    expect(select().value).toBe('model-c');
    expect(select().selectedOptions[0]?.textContent?.trim()).toBe('C');
  });

  it('follows later value changes once options exist', async () => {
    fixture.componentInstance.options.set([
      { id: 'model-a', name: 'A' },
      { id: 'model-b', name: 'B' },
    ]);
    await flush();

    fixture.componentInstance.value.set('model-b');
    await flush();
    expect(select().value).toBe('model-b');
  });

  it('emits valueChange on user selection', async () => {
    fixture.componentInstance.options.set([
      { id: 'model-a', name: 'A' },
      { id: 'model-c', name: 'C' },
    ]);
    await flush();
    select().value = 'model-a';
    select().dispatchEvent(new Event('change'));
    await flush();
    expect(fixture.componentInstance.value()).toBe('model-a');
  });
});
