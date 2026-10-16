import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideTag,
  lucideTags,
  lucideSquareDashed,
  lucideCheck,
  lucideX,
  lucidePlus,
  lucideBan,
  lucideLoader2,
  lucideSparkles,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { TagService } from '../../core/services/tag.service';
import { JobEventService } from '../../core/services/job-event.service';
import { ApiError } from '../../core/services/api.service';
import { FrameTag, ImageTag, TagCategory, TagLearnResponse } from '../../core/models/tag.model';

/** Lightbox section: mark regions, confirm/remove tags, and review the model's suggestions for this frame. */
@Component({
  selector: 'app-frame-tags-panel',
  imports: [NgIcon, HlmButtonImports, HlmInputImports, HlmNativeSelectImports],
  providers: [
    provideIcons({
      lucideTag,
      lucideTags,
      lucideSquareDashed,
      lucideCheck,
      lucideX,
      lucidePlus,
      lucideBan,
      lucideLoader2,
      lucideSparkles,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="flex flex-col gap-2.5 py-3.5 border-b border-border" aria-labelledby="frame-tags-title">
      <div class="flex items-center justify-between gap-2">
        <h5 id="frame-tags-title" class="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
          <ng-icon name="lucideTags" class="size-3.5 text-primary" />
          <span>Image Tags</span>
          @if (tagService.frameLoading() || tagService.busy()) {
            <ng-icon name="lucideLoader2" class="size-3.5 animate-spin" />
          }
        </h5>
        <button
          hlmBtn
          size="xs"
          [variant]="tagService.drawMode() ? 'default' : 'outline'"
          (click)="toggleDraw()"
          class="cursor-pointer gap-1"
          [attr.aria-pressed]="tagService.drawMode()"
        >
          <ng-icon name="lucideSquareDashed" class="size-3.5" />
          <span>{{ tagService.drawMode() ? 'Drawing…' : 'Mark region' }}</span>
          <kbd class="text-xs opacity-70">T</kbd>
        </button>
      </div>

      @if (tagService.drawMode() && !tagService.pendingBox()) {
        <p class="text-xs text-muted-foreground">Drag a box around the element on the image.</p>
      }

      <!-- Name the drawn region -->
      @if (tagService.pendingBox()) {
        <div class="flex flex-col gap-2 rounded-lg border border-primary/40 bg-primary/5 p-2.5">
          <label for="tag-name-input" class="text-xs font-semibold text-foreground">What is in the box?</label>
          <input
            hlmInput
            id="tag-name-input"
            class="h-8 md:h-8 w-full text-sm"
            autocomplete="off"
            placeholder="e.g. red rope, wooden chair"
            [value]="query()"
            (input)="query.set($any($event.target).value)"
            (keydown.enter)="submitPending('positive')"
            (keydown.escape)="cancelPending()"
            #nameInput
          />
          @if (matches().length > 0) {
            <div class="flex flex-wrap gap-1" role="listbox" aria-label="Existing tags">
              @for (t of matches(); track t.id) {
                <button
                  type="button"
                  role="option"
                  [attr.aria-selected]="query().toLowerCase() === t.name.toLowerCase()"
                  class="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5 text-xs hover:bg-muted cursor-pointer"
                  (click)="query.set(t.name)"
                >
                  <span class="size-2 rounded-full" [style.background-color]="t.color"></span>
                  {{ t.name }}
                  <span class="text-muted-foreground tabular-nums">{{ t.positive_count }}</span>
                </button>
              }
            </div>
          }
          @if (!existingMatch() && query().trim()) {
            <div class="flex items-center gap-2">
              <label for="tag-category" class="text-xs text-muted-foreground shrink-0">New tag category</label>
              <hlm-native-select
                size="sm"
                selectId="tag-category"
                class="flex-1"
                [value]="category()"
                (valueChange)="category.set($any($event) || 'object')"
              >
                @for (c of tagService.categories(); track c) {
                  <option [value]="c">{{ c }}</option>
                }
              </hlm-native-select>
            </div>
          }
          <div class="flex items-center gap-1.5">
            <button
              hlmBtn
              size="xs"
              class="flex-1 cursor-pointer gap-1"
              [disabled]="!query().trim() || tagService.busy()"
              (click)="submitPending('positive')"
            >
              <ng-icon name="lucidePlus" class="size-3.5" />
              <span>{{ existingMatch() ? 'Add example' : 'Create tag' }}</span>
            </button>
            <button
              hlmBtn
              size="xs"
              variant="outline"
              class="cursor-pointer gap-1"
              [disabled]="!existingMatch() || tagService.busy()"
              (click)="submitPending('negative')"
              title="Teach the model that this region is NOT the tag"
            >
              <ng-icon name="lucideBan" class="size-3.5" />
              <span>Not this</span>
            </button>
            <button hlmBtn size="xs" variant="ghost" class="cursor-pointer" (click)="cancelPending()" aria-label="Cancel">
              <ng-icon name="lucideX" class="size-3.5" />
            </button>
          </div>
        </div>
      }

      <!-- Confirmed tags -->
      @if (frame(); as f) {
        @if (f.confirmed.length > 0) {
          <ul class="flex flex-wrap gap-1.5" aria-label="Confirmed tags">
            @for (t of f.confirmed; track t.tag_id) {
              <li
                class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold text-foreground"
                [style.border-color]="t.color"
                (mouseenter)="tagService.highlightTagId.set(t.tag_id)"
                (mouseleave)="tagService.highlightTagId.set(null)"
              >
                <span class="size-2 rounded-full" [style.background-color]="t.color"></span>
                {{ t.name }}
                <button
                  type="button"
                  class="ml-0.5 rounded-full p-0.5 text-muted-foreground hover:text-destructive cursor-pointer"
                  [attr.aria-label]="'Remove tag ' + t.name"
                  (click)="remove(t)"
                >
                  <ng-icon name="lucideX" class="size-3" />
                </button>
              </li>
            }
          </ul>
        }

        <!-- Suggestions -->
        @if (f.suggestions.length > 0) {
          <div class="flex flex-col gap-1">
            <span class="flex items-center gap-1 text-xs font-semibold text-muted-foreground">
              <ng-icon name="lucideSparkles" class="size-3.5 text-primary" />
              Suggested — confirm to teach the model
            </span>
            <ul class="flex flex-col gap-1" aria-label="Suggested tags">
              @for (s of f.suggestions; track s.tag_id) {
                <li
                  class="flex items-center gap-2 rounded-md border border-dashed border-border px-2 py-1 text-xs"
                  (mouseenter)="tagService.highlightTagId.set(s.tag_id)"
                  (mouseleave)="tagService.highlightTagId.set(null)"
                >
                  <span class="size-2 shrink-0 rounded-full" [style.background-color]="s.color"></span>
                  <span class="flex-1 truncate font-medium text-foreground">{{ s.name }}</span>
                  <span class="tabular-nums text-muted-foreground">{{ pct(s.score) }}</span>
                  <button
                    hlmBtn
                    size="xs"
                    variant="outline"
                    class="size-6 p-0 cursor-pointer"
                    [disabled]="tagService.busy()"
                    [attr.aria-label]="'Confirm ' + s.name"
                    (click)="accept(s)"
                  >
                    <ng-icon name="lucideCheck" class="size-3.5" />
                  </button>
                  <button
                    hlmBtn
                    size="xs"
                    variant="outline"
                    class="size-6 p-0 cursor-pointer hover:text-destructive"
                    [disabled]="tagService.busy()"
                    [attr.aria-label]="'Reject ' + s.name"
                    (click)="reject(s)"
                  >
                    <ng-icon name="lucideX" class="size-3.5" />
                  </button>
                </li>
              }
            </ul>
          </div>
        }

        @if (f.confirmed.length === 0 && f.suggestions.length === 0 && !tagService.pendingBox()) {
          <p class="text-xs text-muted-foreground">
            No tags yet. Mark a region to teach a tag; it will be suggested on similar frames in every video.
          </p>
        }
      }
    </section>
  `,
})
export class FrameTagsPanelComponent {
  readonly tagService = inject(TagService);
  private readonly jobEvents = inject(JobEventService);

  readonly frameId = input.required<number>();
  /** Emitted after every change, with the frame's new compact tag list (for the grid). */
  readonly changed = output<TagLearnResponse>();

  readonly query = signal('');
  readonly category = signal<TagCategory>('object');
  readonly frame = computed(() => {
    const f = this.tagService.frameTags();
    return f && f.frame_id === this.frameId() ? f : null;
  });
  readonly matches = computed(() => this.tagService.matchTags(this.query(), 8));
  readonly existingMatch = computed<ImageTag | undefined>(() =>
    this.tagService.tagsByName().get(this.query().trim().toLowerCase()),
  );

  private readonly nameInput = viewChild<ElementRef<HTMLInputElement>>('nameInput');

  constructor() {
    // Focus the name field as soon as a box has been drawn
    effect(() => {
      this.nameInput()?.nativeElement.focus();
    });
  }

  pct(score: number | null): string {
    return `${Math.round((score ?? 0) * 100)}%`;
  }

  toggleDraw(): void {
    const on = !this.tagService.drawMode();
    this.tagService.drawMode.set(on);
    if (!on) this.tagService.pendingBox.set(null);
  }

  cancelPending(): void {
    this.tagService.pendingBox.set(null);
    this.tagService.drawMode.set(false);
    this.query.set('');
  }

  async submitPending(polarity: 'positive' | 'negative'): Promise<void> {
    const box = this.tagService.pendingBox();
    const name = this.query().trim();
    if (!box || !name) return;
    const existing = this.existingMatch();
    if (polarity === 'negative' && !existing) return;
    await this.run(() =>
      this.tagService.markRegion(
        this.frameId(),
        box,
        existing ? { tagId: existing.id } : { tagName: name, category: this.category() },
        polarity,
      ),
    );
    this.query.set('');
  }

  accept(s: FrameTag): Promise<void> {
    return this.run(() => this.tagService.acceptSuggestion(this.frameId(), s.tag_id, s.box));
  }

  reject(s: FrameTag): Promise<void> {
    return this.run(() => this.tagService.rejectSuggestion(this.frameId(), s.tag_id, s.box));
  }

  remove(t: FrameTag): Promise<void> {
    return this.run(() => this.tagService.removeFrameTag(this.frameId(), t.tag_id));
  }

  private async run(fn: () => Promise<TagLearnResponse>): Promise<void> {
    try {
      const res = await fn();
      this.changed.emit(res);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      this.jobEvents.showToast('Tagging failed', msg, null, true);
    }
  }
}
