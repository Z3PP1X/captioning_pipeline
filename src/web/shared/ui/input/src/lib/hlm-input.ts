import type { BooleanInput } from '@angular/cdk/coercion';
import { booleanAttribute, computed, Directive, inject, input } from '@angular/core';
import {
  BrnFieldControl,
  BrnFieldControlDescribedBy,
  provideBrnLabelable,
} from '@spartan-ng/brain/field';
import { classes } from '@spartan-ng/helm/utils';

let nextId = 0;

/**
 * Brain-level state of the input (drop-in for `BrnInput` from `@spartan-ng/brain/input`):
 * id, labelable registration, field control + data attributes and a touched-aware `aria-invalid`.
 */
@Directive({
  selector: '[hlmInputControl]',
  providers: [provideBrnLabelable(HlmInputControl)],
  hostDirectives: [BrnFieldControl],
  host: {
    '[id]': 'id()',
    '[attr.aria-invalid]': '_spartanInvalid() ? "true" : null',
    '[attr.data-invalid]': '_fieldControl.invalid() ? "true" : null',
    '[attr.data-touched]': '_fieldControl.touched() ? "true" : null',
    '[attr.data-dirty]': '_fieldControl.dirty() ? "true" : null',
    '[attr.data-matches-spartan-invalid]': '_spartanInvalid() ? "true" : null',
  },
})
export class HlmInputControl {
  protected readonly _fieldControl = inject(BrnFieldControl, { self: true });

  /** The id of the input. */
  public readonly id = input(`hlm-input-${++nextId}`);
  /** Whether to force the input into an invalid state. */
  public readonly forceInvalid = input<boolean, BooleanInput>(false, {
    transform: booleanAttribute,
  });

  /** Read by `BrnField` / `hlmFieldLabel` to wire `label[for]`. */
  public readonly labelableId = this.id;

  protected readonly _spartanInvalid = computed(
    () => this.forceInvalid() || !!this._fieldControl.spartanInvalid(),
  );
}

/**
 * Project adaptation: `HlmInputControl` replaces `BrnInput` (`@spartan-ng/brain/input`) as the
 * host directive (same inputs, attributes and labelable registration). `BrnInput` binds
 * `aria-invalid` to the raw control `invalid()`; with `formControlName` the control resolves one
 * tick after the first render, so that binding flips to `"true"` later and overrides any
 * touched-aware binding on the same host (pristine empty required fields were announced as
 * invalid). Here `aria-invalid` has a single source: `BrnFieldControl.spartanInvalid()`
 * (touched/submitted) or `forceInvalid`.
 */
@Directive({
  selector: '[hlmInput]',
  hostDirectives: [
    { directive: HlmInputControl, inputs: ['id', 'forceInvalid'] },
    BrnFieldControlDescribedBy,
  ],
  host: { 'data-slot': 'input' },
})
export class HlmInput {
  constructor() {
    classes(
      () =>
        'dark:bg-input/30 border-input focus-visible:border-ring focus-visible:ring-ring data-[matches-spartan-invalid=true]:ring-destructive/20 dark:data-[matches-spartan-invalid=true]:ring-destructive/40 data-[matches-spartan-invalid=true]:border-destructive dark:data-[matches-spartan-invalid=true]:border-destructive/50 h-11 md:h-9 rounded-md border bg-transparent px-2.5 py-1 text-base shadow-xs transition-[color,box-shadow] file:h-7 file:text-sm file:font-medium focus-visible:ring-3 data-[matches-spartan-invalid=true]:ring-3 md:text-sm file:text-foreground placeholder:text-muted-foreground w-full min-w-0 outline-none file:inline-flex file:border-0 file:bg-transparent disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
    );
  }
}
