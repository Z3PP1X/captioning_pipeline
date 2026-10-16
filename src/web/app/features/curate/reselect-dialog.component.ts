import { ChangeDetectionStrategy, Component, OnInit, computed, inject, output, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormField, form } from '@angular/forms/signals';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideShuffle,
  lucideTriangleAlert,
  lucideCircleAlert,
  lucideLoader2,
  lucideBrain,
  lucideCheck,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmAlertImports } from '@spartan-ng/helm/alert';
import { HlmCheckboxImports } from '@spartan-ng/helm/checkbox';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { HlmInputImports } from '@spartan-ng/helm/input';
import { CurateService } from '../../core/services/curate.service';
import { VideoService } from '../../core/services/video.service';
import { ChunkService } from '../../core/services/chunk.service';
import { SettingsService } from '../../core/services/settings.service';
import { ScoringService } from '../../core/services/scoring.service';
import { JobEventService } from '../../core/services/job-event.service';
import { registerModal } from '../../core/services/ui-state.service';
import { RESELECT_AXES, ReselectAxis } from '../../core/models/scoring.model';
import {
  AXIS_LABELS,
  AxisCapsModel,
  ReselectField,
  ReselectFormModel,
  axisCapErrors,
  buildAxisCaps,
  buildReselectRequest,
  formatReselectSummary,
  reselectErrorMessage,
  reselectFormErrors,
} from '../../core/utils/reselect.utils';

interface KnobView {
  key: ReselectField;
  id: string;
  label: string;
  help: string;
  step: number;
  placeholder: string;
  inputmode: 'numeric' | 'decimal';
}

/**
 * Phase F "Reselect" knobs (POST /api/videos/{id}/reselect) plus the learned-weights status / fit / opt-in.
 * Rendered behind an `@if` by the Curate toolbar: the dialog is open while the component exists.
 */
@Component({
  selector: 'app-reselect-dialog',
  imports: [
    DecimalPipe,
    NgIcon,
    FormField,
    HlmButtonImports,
    HlmBadgeImports,
    HlmAlertImports,
    HlmCheckboxImports,
    HlmDialogImports,
    HlmInputImports,
  ],
  providers: [
    provideIcons({ lucideShuffle, lucideTriangleAlert, lucideCircleAlert, lucideLoader2, lucideBrain, lucideCheck }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hlm-dialog state="open" (closed)="close.emit()">
      <hlm-dialog-content
        *hlmDialogPortal="let ctx"
        class="sm:max-w-lg w-full p-0 gap-0 flex flex-col max-h-[90vh] overflow-hidden"
        closeLabel="Close reselect"
      >
        <hlm-dialog-header class="border-b border-border px-6 py-4 pr-14">
          <h3 hlmDialogTitle class="font-sans font-bold text-base text-foreground flex items-center gap-2">
            <ng-icon name="lucideShuffle" class="size-4 text-muted-foreground" aria-hidden="true" />
            Reselect picks
          </h3>
          <p hlmDialogDescription class="text-xs text-muted-foreground">
            Re-runs pick vs runner-up over the stored frames (no decode). Starred and exported frames stay picks,
            rejected frames are never picked.
          </p>
        </hlm-dialog-header>

        <form
          id="reselect-form"
          class="flex flex-col gap-4 p-6 overflow-y-auto text-sm"
          novalidate
          (submit)="$event.preventDefault(); onSubmit()"
        >
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            @for (k of knobs; track k.key) {
              @let err = errors()[k.key];
              <div class="flex flex-col gap-1" [class.sm:col-span-2]="k.key === 'target_count'">
                <label [for]="k.id" class="text-xs font-medium text-foreground">{{ k.label }}</label>
                <input
                  hlmInput
                  [id]="k.id"
                  type="number"
                  [attr.inputmode]="k.inputmode"
                  [attr.step]="k.step"
                  [placeholder]="k.placeholder"
                  [formField]="reselectForm[k.key]"
                  [forceInvalid]="!!err"
                  [aria-describedby]="k.id + (err ? '-error' : '-help')"
                  class="tabular-nums"
                />
                @if (err) {
                  <span [id]="k.id + '-error'" class="flex items-center gap-1 text-xs font-medium text-destructive">
                    <ng-icon name="lucideTriangleAlert" class="size-3 shrink-0" aria-hidden="true" />
                    {{ err }}
                  </span>
                } @else {
                  <span [id]="k.id + '-help'" class="text-xs text-muted-foreground">{{ k.help }}</span>
                }
              </div>
            }
          </div>

          <!-- Phase F2: category axis caps and cross-dataset dedup -->
          <fieldset class="flex flex-col gap-3 rounded-lg border border-border p-4">
            <legend class="px-1 text-xs font-semibold text-foreground">Advanced</legend>
            <p class="text-xs text-muted-foreground">Max share of picks per category (0-1, empty = no cap).</p>
            <div class="grid grid-cols-2 gap-3">
              @for (axis of axes; track axis) {
                @let aerr = axisErrors()[axis];
                <div class="flex flex-col gap-1">
                  <label [for]="'reselect-cap-' + axis" class="text-xs font-medium text-foreground">{{ axisLabels[axis] }}</label>
                  <input
                    hlmInput
                    [id]="'reselect-cap-' + axis"
                    type="number"
                    inputmode="decimal"
                    step="0.05"
                    min="0"
                    max="1"
                    placeholder="no cap"
                    class="tabular-nums"
                    [value]="axisCaps()[axis] ?? ''"
                    (input)="onAxisCap(axis, $any($event.target).value)"
                    [attr.aria-invalid]="aerr ? 'true' : null"
                    [attr.aria-describedby]="aerr ? 'reselect-cap-' + axis + '-error' : null"
                  />
                  @if (aerr) {
                    <span [id]="'reselect-cap-' + axis + '-error'" class="text-xs font-medium text-destructive">{{ aerr }}</span>
                  }
                </div>
              }
            </div>
            <div class="flex items-center gap-2">
              <hlm-checkbox
                inputId="reselect-cross-dataset"
                [checked]="crossDataset()"
                (checkedChange)="crossDataset.set($event)"
              />
              <label for="reselect-cross-dataset" class="text-xs font-medium">
                Skip frames already in the dataset from other videos
              </label>
            </div>
          </fieldset>

          @if (requestError(); as msg) {
            <div hlmAlert variant="destructive" role="alert">
              <ng-icon name="lucideCircleAlert" />
              <h4 hlmAlertTitle>Reselect failed</h4>
              <p hlmAlertDescription>{{ msg }}</p>
            </div>
          }

          <!-- Learned weights (Phase F): status, fit, opt-in -->
          <section class="rounded-lg border border-border bg-muted/40 p-4 flex flex-col gap-3" aria-labelledby="lw-title">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h4 id="lw-title" class="text-sm font-semibold flex items-center gap-2">
                <ng-icon name="lucideBrain" class="size-4 text-muted-foreground" aria-hidden="true" />
                Learned weights
              </h4>
              @if (lw(); as s) {
                @if (s.active) {
                  <span hlmBadge variant="success"><ng-icon name="lucideCheck" aria-hidden="true" /> Active</span>
                } @else if (s.fitted) {
                  <span hlmBadge variant="muted">Fitted, not in use</span>
                } @else {
                  <span hlmBadge variant="muted">Not fitted</span>
                }
              }
            </div>

            @if (lw(); as s) {
              <dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <dt class="text-muted-foreground">Labelled keep/reject events</dt>
                <dd class="tabular-nums text-right font-medium">{{ s.n_events }} <span class="text-muted-foreground">/ {{ s.min_events }} needed</span></dd>
                <dt class="text-muted-foreground">Training accuracy</dt>
                <dd class="tabular-nums text-right font-medium">
                  {{ s.accuracy !== null && s.accuracy !== undefined ? (s.accuracy * 100 | number: '1.0-1') + '%' : '-' }}
                </dd>
              </dl>
              <p class="text-xs text-muted-foreground">
                Accuracy is measured on the training events, not held out. Fitting stores the weights; they only
                affect scores and reselect while "Use learned weights" is on.
              </p>
            } @else if (scoring.isLoading()) {
              <p class="text-xs text-muted-foreground flex items-center gap-1">
                <ng-icon name="lucideLoader2" class="size-3 motion-safe:animate-spin" aria-hidden="true" /> Loading status...
              </p>
            }

            @if (scoring.error(); as e) {
              <p class="text-xs font-medium text-destructive flex items-start gap-1" role="alert">
                <ng-icon name="lucideTriangleAlert" class="size-3 shrink-0 mt-0.5" aria-hidden="true" /> {{ e }}
              </p>
            }

            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <hlm-checkbox
                  inputId="reselect-use-learned"
                  [checked]="useLearned()"
                  [disabled]="scoring.isSaving()"
                  (checkedChange)="onToggleLearned($event)"
                />
                <label for="reselect-use-learned" class="text-xs font-medium">Use learned weights</label>
              </div>
              <div class="flex flex-col items-end gap-0.5">
                <button
                  hlmBtn
                  variant="outline"
                  size="sm"
                  type="button"
                  (click)="scoring.fitLearnedWeights()"
                  [disabled]="scoring.isFitting() || !lw()?.can_fit"
                  aria-describedby="lw-fit-reason"
                >
                  @if (scoring.isFitting()) {
                    <ng-icon name="lucideLoader2" class="size-4 motion-safe:animate-spin" aria-hidden="true" />
                  } @else {
                    <ng-icon name="lucideBrain" class="size-4" aria-hidden="true" />
                  }
                  Fit now
                </button>
                @if (lw(); as s) {
                  @if (!s.can_fit) {
                    <span id="lw-fit-reason" class="text-xs text-muted-foreground">
                      Needs {{ s.min_events }} labelled events of both kinds
                    </span>
                  }
                }
              </div>
            </div>
          </section>
        </form>

        <hlm-dialog-footer class="border-t border-border px-6 py-3 flex-row justify-end gap-2">
          <button hlmBtn variant="ghost" size="sm" type="button" (click)="close.emit()">Cancel</button>
          <button
            hlmBtn
            size="sm"
            type="submit"
            form="reselect-form"
            [disabled]="isSubmitting() || hasErrors() || !videoService.currentVideo()"
          >
            @if (isSubmitting()) {
              <ng-icon name="lucideLoader2" class="size-4 motion-safe:animate-spin" aria-hidden="true" />
              Reselecting...
            } @else {
              <ng-icon name="lucideShuffle" class="size-4" aria-hidden="true" />
              Reselect
            }
          </button>
        </hlm-dialog-footer>
      </hlm-dialog-content>
    </hlm-dialog>
  `,
})
export class ReselectDialogComponent implements OnInit {
  readonly curateService = inject(CurateService);
  readonly videoService = inject(VideoService);
  private readonly chunkService = inject(ChunkService);
  private readonly settingsService = inject(SettingsService);
  readonly scoring = inject(ScoringService);
  private readonly jobEvents = inject(JobEventService);

  readonly close = output<void>();

  readonly knobs: readonly KnobView[] = [
    {
      key: 'target_count',
      id: 'reselect-target',
      label: 'Target picks',
      help: 'Total picks after reselect (default: the current pick count).',
      step: 1,
      placeholder: 'current pick count',
      inputmode: 'numeric',
    },
    {
      key: 'diversity',
      id: 'reselect-diversity',
      label: 'Diversity (0-1)',
      help: '0 = best quality only, 1 = maximum visual coverage.',
      step: 0.05,
      placeholder: 'server default',
      inputmode: 'decimal',
    },
    {
      key: 'elasticity',
      id: 'reselect-elasticity',
      label: 'Elasticity (0-1)',
      help: 'How far a chunk may drift from its budget share.',
      step: 0.05,
      placeholder: 'server default',
      inputmode: 'decimal',
    },
    {
      key: 'per_chunk_floor',
      id: 'reselect-floor',
      label: 'Per-chunk floor',
      help: 'Minimum picks in every chunk (empty = from elasticity).',
      step: 1,
      placeholder: 'auto',
      inputmode: 'numeric',
    },
    {
      key: 'per_chunk_cap',
      id: 'reselect-cap',
      label: 'Per-chunk cap',
      help: 'Maximum picks in every chunk (empty = from elasticity).',
      step: 1,
      placeholder: 'auto',
      inputmode: 'numeric',
    },
  ];

  readonly model = signal<ReselectFormModel>(this.initialModel());
  readonly reselectForm = form(this.model);
  readonly isSubmitting = signal(false);
  readonly requestError = signal<string | null>(null);

  private readonly chunkCount = computed(
    () => this.curateService.curationChunks().length || this.chunkService.chunks().length,
  );
  readonly errors = computed(() => reselectFormErrors(this.model(), this.chunkCount()));
  readonly axes = RESELECT_AXES;
  readonly axisLabels = AXIS_LABELS;
  readonly axisCaps = signal<AxisCapsModel>(this.initialAxisCaps());
  readonly crossDataset = signal<boolean>(this.settingsService.settings().reselect_cross_dataset_dedup ?? false);
  readonly axisErrors = computed(() => axisCapErrors(this.axisCaps()));
  readonly hasErrors = computed(
    () => Object.keys(this.errors()).length > 0 || Object.keys(this.axisErrors()).length > 0,
  );

  private initialAxisCaps(): AxisCapsModel {
    const stored = this.settingsService.settings().reselect_axis_caps ?? {};
    return {
      multi_person: stored.multi_person ?? null,
      solo: stored.solo ?? null,
      equipment: stored.equipment ?? null,
      face: stored.face ?? null,
    };
  }

  onAxisCap(axis: ReselectAxis, raw: string): void {
    const v = raw.trim() === '' ? null : Number(raw);
    this.axisCaps.update((caps) => ({ ...caps, [axis]: v }));
  }

  readonly lw = this.scoring.learnedWeights;
  readonly useLearned = computed(
    () => this.settingsService.settings().use_learned_weights ?? this.lw()?.use_learned_weights ?? false,
  );

  constructor() {
    registerModal('reselect');
  }

  ngOnInit(): void {
    void this.scoring.loadLearnedWeights();
  }

  private initialModel(): ReselectFormModel {
    const s = this.settingsService.settings();
    const picks = this.curateService.candidates().filter((c) => c.is_pick).length;
    return {
      target_count: picks > 0 ? picks : null,
      diversity: s.reselect_diversity ?? 0.5,
      elasticity: s.reselect_elasticity ?? 0.2,
      per_chunk_floor: null,
      per_chunk_cap: null,
    };
  }

  async onToggleLearned(enabled: boolean): Promise<void> {
    await this.scoring.setUseLearnedWeights(enabled);
  }

  async onSubmit(): Promise<void> {
    const video = this.videoService.currentVideo();
    if (!video || this.isSubmitting() || this.hasErrors()) return;
    this.isSubmitting.set(true);
    this.requestError.set(null);
    try {
      const report = await this.curateService.reselect(video.id, {
        ...buildReselectRequest(this.model()),
        axis_caps: buildAxisCaps(this.axisCaps()),
        cross_dataset_dedup: this.crossDataset(),
      });
      if (report) {
        const summary = formatReselectSummary(report);
        this.jobEvents.showToast(summary.title, summary.message, video.id, false, null, summary.isWarning);
      } else {
        this.jobEvents.showToast('Picks reselected', 'The candidate list was updated.', video.id);
      }
      this.close.emit();
    } catch (err) {
      this.requestError.set(reselectErrorMessage(err));
    } finally {
      this.isSubmitting.set(false);
    }
  }
}
