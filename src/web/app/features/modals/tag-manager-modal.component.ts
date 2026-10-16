import { ChangeDetectionStrategy, Component, OnInit, computed, inject, output, signal } from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideTags,
  lucideX,
  lucideTrash2,
  lucideSearch,
  lucideSparkles,
  lucideGitMerge,
  lucideSave,
  lucideLoader2,
  lucideImageOff,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { HlmNativeSelectImports } from '@spartan-ng/helm/native-select';
import { TagService } from '../../core/services/tag.service';
import { VideoService } from '../../core/services/video.service';
import { JobEventService } from '../../core/services/job-event.service';
import { ApiError } from '../../core/services/api.service';
import { ImageTag, TagCategory, TagRegion, VideoTagSummary } from '../../core/models/tag.model';
import { registerModal } from '../../core/services/ui-state.service';

/** Tag library: what the model has learned per tag, its examples, and housekeeping (rename, merge, delete). */
@Component({
  selector: 'app-tag-manager-modal',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmDialogImports, HlmInputImports, HlmNativeSelectImports],
  providers: [
    provideIcons({
      lucideTags,
      lucideX,
      lucideTrash2,
      lucideSearch,
      lucideSparkles,
      lucideGitMerge,
      lucideSave,
      lucideLoader2,
      lucideImageOff,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <!-- Rendered behind @if in the shell: the dialog is open for as long as the component exists -->
    <hlm-dialog state="open" (closed)="close.emit()">
      <hlm-dialog-content
        *hlmDialogPortal="let ctx"
        class="sm:max-w-4xl w-full p-0 gap-0 flex flex-col max-h-[90vh] overflow-hidden bg-card"
        closeLabel="Close"
      >
        <!-- Header -->
        <hlm-dialog-header class="flex-row items-center gap-2.5 border-b border-border px-5 py-3.5 pr-14">
          <div class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ng-icon name="lucideTags" class="size-4" />
          </div>
          <div class="flex flex-col gap-1">
            <h3 hlmDialogTitle class="font-bold text-base text-foreground">Tag library</h3>
            <p hlmDialogDescription class="text-xs text-muted-foreground">
              Every region you mark, confirm or reject teaches the tag. Tags are shared by all videos.
            </p>
          </div>
        </hlm-dialog-header>

        <div class="flex flex-1 min-h-0 flex-col md:flex-row">
          <!-- Tag list -->
          <aside class="flex flex-col gap-2 border-b md:border-b-0 md:border-r border-border p-3 md:w-72 min-h-0">
            <div class="relative">
              <ng-icon name="lucideSearch" class="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
              <label for="tag-search" class="sr-only">Search tags</label>
              <input
                hlmInput
                id="tag-search"
                class="h-8 md:h-8 w-full pl-7 text-sm"
                placeholder="Search tags"
                [value]="search()"
                (input)="search.set($any($event.target).value)"
              />
            </div>
            <ul class="flex flex-col gap-1 overflow-y-auto min-h-0 max-h-60 md:max-h-none" aria-label="Tags">
              @for (t of filtered(); track t.id) {
                <li>
                  <button
                    type="button"
                    class="flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left text-sm cursor-pointer hover:bg-muted"
                    [class.border-primary]="selectedId() === t.id"
                    [class.bg-primary/5]="selectedId() === t.id"
                    [class.border-transparent]="selectedId() !== t.id"
                    [attr.aria-current]="selectedId() === t.id ? 'true' : null"
                    (click)="select(t)"
                  >
                    @if (t.cover_url) {
                      <img [src]="t.cover_url" alt="" class="size-9 shrink-0 rounded-md object-cover bg-muted" loading="lazy" />
                    } @else {
                      <span class="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <ng-icon name="lucideImageOff" class="size-4" />
                      </span>
                    }
                    <span class="flex min-w-0 flex-1 flex-col">
                      <span class="flex items-center gap-1.5 font-semibold text-foreground">
                        <span class="size-2 shrink-0 rounded-full" [style.background-color]="t.color"></span>
                        <span class="truncate">{{ t.name }}</span>
                      </span>
                      <span class="text-xs text-muted-foreground tabular-nums">
                        {{ t.category }} · {{ t.positive_count }} examples · {{ t.confirmed_count }} frames
                      </span>
                    </span>
                  </button>
                </li>
              } @empty {
                <li class="px-2 py-6 text-center text-xs text-muted-foreground">
                  @if (tagService.tags().length === 0) {
                    No tags yet. Open a frame in the Curation grid, press <kbd>T</kbd> and drag a box around an element.
                  } @else {
                    No tag matches "{{ search() }}".
                  }
                </li>
              }
            </ul>
          </aside>

          <!-- Detail -->
          <section class="flex flex-1 min-h-0 flex-col gap-4 overflow-y-auto p-5" aria-live="polite">
            @if (selected(); as t) {
              <div class="flex flex-wrap items-center gap-2">
                <h4 class="font-bold text-lg text-foreground">{{ t.name }}</h4>
                <span hlmBadge variant="outline" class="text-xs">{{ t.category }}</span>
                @if (t.accuracy !== null) {
                  <span hlmBadge [variant]="t.accuracy >= 0.85 ? 'success' : 'warning'" class="text-xs tabular-nums">
                    Learned {{ Math.round(t.accuracy * 100) }}%
                  </span>
                } @else {
                  <span hlmBadge variant="secondary" class="text-xs">Learning: add examples and reject wrong suggestions</span>
                }
              </div>

              <dl class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div class="rounded-lg bg-muted p-2"><dt class="text-muted-foreground">Examples</dt><dd class="font-bold tabular-nums">{{ t.positive_count }}</dd></div>
                <div class="rounded-lg bg-muted p-2"><dt class="text-muted-foreground">Counter-examples</dt><dd class="font-bold tabular-nums">{{ t.negative_count }}</dd></div>
                <div class="rounded-lg bg-muted p-2"><dt class="text-muted-foreground">Confirmed frames</dt><dd class="font-bold tabular-nums">{{ t.confirmed_count }}</dd></div>
                <div class="rounded-lg bg-muted p-2"><dt class="text-muted-foreground">Open suggestions</dt><dd class="font-bold tabular-nums">{{ t.suggested_count }}</dd></div>
              </dl>

              <!-- Edit -->
              <div class="grid gap-3 sm:grid-cols-2">
                <div class="flex flex-col gap-1">
                  <label for="tag-edit-name" class="text-xs font-semibold text-foreground">Name</label>
                  <input hlmInput id="tag-edit-name" class="h-8 md:h-8 text-sm" [value]="editName()" (input)="editName.set($any($event.target).value)" />
                </div>
                <div class="flex flex-col gap-1">
                  <label for="tag-edit-category" class="text-xs font-semibold text-foreground">Category</label>
                  <hlm-native-select size="sm" selectId="tag-edit-category" [value]="editCategory()" (valueChange)="editCategory.set($any($event) || 'object')">
                    @for (c of tagService.categories(); track c) {
                      <option [value]="c">{{ c }}</option>
                    }
                  </hlm-native-select>
                </div>
                <div class="flex flex-col gap-1 sm:col-span-2">
                  <label for="tag-edit-prompt" class="text-xs font-semibold text-foreground">Description for zero-shot hints</label>
                  <input hlmInput id="tag-edit-prompt" class="h-8 md:h-8 text-sm" [value]="editPrompt()" (input)="editPrompt.set($any($event.target).value)" />
                  <span class="text-xs text-muted-foreground">Used while the tag has few examples, e.g. "a photo of a red rope harness".</span>
                </div>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <button hlmBtn size="sm" class="cursor-pointer gap-1.5" [disabled]="saving() || !dirty()" (click)="save(t)">
                  <ng-icon name="lucideSave" class="size-3.5" />
                  Save
                </button>

                <label for="tag-merge" class="sr-only">Merge into tag</label>
                <hlm-native-select size="sm" selectId="tag-merge" [value]="mergeInto()" (valueChange)="mergeInto.set($event || '')">
                  <option value="">Merge into…</option>
                  @for (o of mergeTargets(); track o.id) {
                    <option [value]="o.id">{{ o.name }}</option>
                  }
                </hlm-native-select>
                <button hlmBtn size="sm" variant="outline" class="cursor-pointer gap-1.5" [disabled]="!mergeInto() || saving()" (click)="merge(t)">
                  <ng-icon name="lucideGitMerge" class="size-3.5" />
                  Merge
                </button>

                <span class="flex-1"></span>
                @if (confirmDelete()) {
                  <span class="text-xs text-destructive font-semibold">Delete "{{ t.name }}" and all its examples?</span>
                  <button hlmBtn size="sm" variant="destructive" class="cursor-pointer" (click)="remove(t)">Delete</button>
                  <button hlmBtn size="sm" variant="ghost" class="cursor-pointer" (click)="confirmDelete.set(false)">Cancel</button>
                } @else {
                  <button hlmBtn size="sm" variant="ghost" class="cursor-pointer gap-1.5 hover:text-destructive" (click)="confirmDelete.set(true)">
                    <ng-icon name="lucideTrash2" class="size-3.5" />
                    Delete tag
                  </button>
                }
              </div>

              <!-- Examples -->
              @for (group of regionGroups(); track group.label) {
                <div class="flex flex-col gap-2">
                  <h5 class="text-xs font-bold uppercase tracking-wider text-muted-foreground">{{ group.label }} ({{ group.items.length }})</h5>
                  @if (group.items.length === 0) {
                    <p class="text-xs text-muted-foreground">{{ group.empty }}</p>
                  }
                  <ul class="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-8 gap-2">
                    @for (r of group.items; track r.id) {
                      <li class="group relative">
                        <img
                          [src]="r.crop_url"
                          [alt]="(r.polarity === 'negative' ? 'Counter-example' : 'Example') + ' from ' + (r.video_name || 'a deleted frame')"
                          class="aspect-square w-full rounded-md object-cover bg-muted border border-border"
                          [class.opacity-70]="r.polarity === 'negative'"
                          loading="lazy"
                          [title]="(r.video_name || 'frame no longer extracted') + ' · ' + r.source"
                        />
                        <button
                          type="button"
                          class="absolute right-1 top-1 hidden group-hover:flex group-focus-within:flex size-6 items-center justify-center rounded-full bg-background/90 text-muted-foreground hover:text-destructive cursor-pointer"
                          [attr.aria-label]="'Forget this ' + (r.polarity === 'negative' ? 'counter-example' : 'example')"
                          (click)="forget(r)"
                        >
                          <ng-icon name="lucideX" class="size-3.5" />
                        </button>
                      </li>
                    }
                  </ul>
                </div>
              }
            } @else {
              <p class="text-sm text-muted-foreground">Select a tag to see what the model has learned.</p>
            }

            <!-- Current video -->
            @if (videoService.currentVideo(); as v) {
              <div class="mt-auto flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-3">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-xs font-semibold text-foreground truncate">This video: {{ v.name }}</span>
                  <button
                    hlmBtn
                    size="xs"
                    variant="outline"
                    class="cursor-pointer gap-1"
                    [disabled]="tagService.tags().length === 0 || autoTagging()"
                    (click)="autoTag(v.id)"
                  >
                    <ng-icon name="lucideSparkles" class="size-3.5" />
                    Auto-tag all frames
                  </button>
                </div>
                @if (videoSummary().length > 0) {
                  <ul class="flex flex-wrap gap-1.5 text-xs">
                    @for (s of videoSummary(); track s.tag_id) {
                      <li class="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5">
                        <span class="size-2 rounded-full" [style.background-color]="s.color"></span>
                        {{ s.name }}
                        <span class="tabular-nums text-muted-foreground">{{ s.confirmed }} confirmed · {{ s.suggested }} suggested</span>
                      </li>
                    }
                  </ul>
                } @else {
                  <p class="text-xs text-muted-foreground">No tags on this video yet.</p>
                }
              </div>
            }
          </section>
        </div>
      </hlm-dialog-content>
    </hlm-dialog>
  `,
})
export class TagManagerModalComponent implements OnInit {
  readonly tagService = inject(TagService);
  readonly videoService = inject(VideoService);
  private readonly jobEvents = inject(JobEventService);
  readonly close = output<void>();
  readonly Math = Math;

  constructor() {
    // Suppresses global shortcuts while the tag library is open (rendered behind @if in the shell)
    registerModal('tag-manager');
  }

  readonly search = signal('');
  readonly selectedId = signal<number | null>(null);
  readonly regions = signal<TagRegion[]>([]);
  readonly videoSummary = signal<VideoTagSummary[]>([]);
  readonly editName = signal('');
  readonly editCategory = signal<TagCategory>('object');
  readonly editPrompt = signal('');
  readonly mergeInto = signal<string>('');
  readonly confirmDelete = signal(false);
  readonly saving = signal(false);
  readonly autoTagging = signal(false);

  readonly filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    const list = this.tagService.tags();
    return q ? list.filter((t) => t.name.toLowerCase().includes(q) || t.category.includes(q)) : list;
  });
  readonly selected = computed(() => this.tagService.tags().find((t) => t.id === this.selectedId()) ?? null);
  readonly mergeTargets = computed(() => this.tagService.tags().filter((t) => t.id !== this.selectedId()));
  readonly dirty = computed(() => {
    const t = this.selected();
    return !!t && (this.editName().trim() !== t.name || this.editCategory() !== t.category || this.editPrompt().trim() !== t.prompt);
  });
  readonly regionGroups = computed(() => {
    const all = this.regions();
    return [
      { label: 'Examples', empty: 'Mark regions or confirm suggestions to add examples.', items: all.filter((r) => r.polarity === 'positive') },
      { label: 'Counter-examples', empty: 'Rejected suggestions appear here and keep the tag from firing on look-alikes.', items: all.filter((r) => r.polarity === 'negative') },
    ];
  });

  async ngOnInit(): Promise<void> {
    const tags = await this.tagService.loadTags();
    if (tags.length) this.select(tags[0]);
    this.loadVideoSummary();
  }

  async select(t: ImageTag): Promise<void> {
    this.selectedId.set(t.id);
    this.editName.set(t.name);
    this.editCategory.set(t.category);
    this.editPrompt.set(t.prompt);
    this.mergeInto.set('');
    this.confirmDelete.set(false);
    this.regions.set([]);
    try {
      const detail = await this.tagService.getTagDetail(t.id);
      if (this.selectedId() === t.id) this.regions.set(detail.regions);
    } catch (err) {
      this.fail(err);
    }
  }

  private async loadVideoSummary(): Promise<void> {
    const v = this.videoService.currentVideo();
    if (!v) return;
    try {
      this.videoSummary.set(await this.tagService.getVideoSummary(v.id));
    } catch {
      this.videoSummary.set([]);
    }
  }

  async save(t: ImageTag): Promise<void> {
    this.saving.set(true);
    try {
      const updated = await this.tagService.updateTag(t.id, {
        name: this.editName().trim(),
        category: this.editCategory(),
        prompt: this.editPrompt().trim(),
      });
      this.select(updated);
    } catch (err) {
      this.fail(err);
    } finally {
      this.saving.set(false);
    }
  }

  async merge(t: ImageTag): Promise<void> {
    const into = Number(this.mergeInto());
    if (!into) return;
    this.saving.set(true);
    try {
      const target = await this.tagService.mergeTag(t.id, into);
      this.select(target);
      this.loadVideoSummary();
    } catch (err) {
      this.fail(err);
    } finally {
      this.saving.set(false);
    }
  }

  async remove(t: ImageTag): Promise<void> {
    try {
      await this.tagService.deleteTag(t.id);
      this.selectedId.set(null);
      this.regions.set([]);
      const first = this.tagService.tags()[0];
      if (first) this.select(first);
      this.loadVideoSummary();
    } catch (err) {
      this.fail(err);
    }
  }

  async forget(r: TagRegion): Promise<void> {
    try {
      await this.tagService.deleteRegion(r.id);
      this.regions.update((list) => list.filter((x) => x.id !== r.id));
    } catch (err) {
      this.fail(err);
    }
  }

  async autoTag(videoId: string): Promise<void> {
    this.autoTagging.set(true);
    try {
      await this.tagService.suggestForVideo(videoId);
      this.jobEvents.showToast('Auto-tagging started', 'Suggestions appear in the grid when the job finishes.');
    } catch (err) {
      this.fail(err);
    } finally {
      this.autoTagging.set(false);
    }
  }

  private fail(err: unknown): void {
    this.jobEvents.showToast('Tag library', err instanceof ApiError ? err.message : String(err), null, true);
  }
}
