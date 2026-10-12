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
 * Brain-level state of the textarea (drop-in for `BrnTextarea` from `@spartan-ng/brain/textarea`):
 * id, labelable registration, field control + data attributes and a touched-aware `aria-invalid`.
 */
@Directive({
  selector: '[hlmTextareaControl]',
  providers: [provideBrnLabelable(HlmTextareaControl)],
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
export class HlmTextareaControl {
  protected readonly _fieldControl = inject(BrnFieldControl, { self: true });

  /** The id of the textarea. */
  public readonly id = input(`hlm-textarea-${++nextId}`);
  /** Whether to force the textarea into an invalid state. */
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
 * Project adaptation: `HlmTextareaControl` replaces `BrnTextarea` (`@spartan-ng/brain/textarea`)
 * as the host directive, because its raw `invalid()` `aria-invalid` binding overrides any
 * touched-aware binding once a `formControlName` control resolves (see `HlmInput`). `aria-invalid`
 * follows `BrnFieldControl.spartanInvalid()` (touched/submitted) or `forceInvalid` only.
 */
@Directive({
  selector: '[hlmTextarea]',
  hostDirectives: [
    { directive: HlmTextareaControl, inputs: ['id', 'forceInvalid'] },
    BrnFieldControlDescribedBy,
  ],
  host: { 'data-slot': 'textarea' },
})
export class HlmTextarea {
  constructor() {
    classes(
      () =>
        'border-input dark:bg-input/30 focus-visible:border-ring focus-visible:ring-ring data-[matches-spartan-invalid=true]:ring-destructive/20 dark:data-[matches-spartan-invalid=true]:ring-destructive/40 data-[matches-spartan-invalid=true]:border-destructive dark:data-[matches-spartan-invalid=true]:border-destructive/50 rounded-md border bg-transparent px-2.5 py-2 text-base shadow-xs transition-[color,box-shadow] focus-visible:ring-3 data-[matches-spartan-invalid=true]:ring-3 md:text-sm placeholder:text-muted-foreground flex field-sizing-content min-h-16 w-full outline-none disabled:cursor-not-allowed disabled:opacity-50',
    );
  }
}
