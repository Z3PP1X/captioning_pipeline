import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
  OnInit,
} from '@angular/core';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideUser,
  lucideUserPlus,
  lucideUserCheck,
  lucideCheck,
  lucideTrash2,
  lucideZap,
  lucideTarget,
  lucideLoader2,
  lucidePlus,
  lucideBan,
  lucideMinus,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { HlmDialogImports } from '@spartan-ng/helm/dialog';
import { PersonService } from '../../core/services/person.service';
import { VideoService } from '../../core/services/video.service';
import { CurateService } from '../../core/services/curate.service';
import { Person, DetectedFace } from '../../core/models/person.model';
import { ConfirmService } from '../../core/services/confirm.service';
import { registerModal } from '../../core/services/ui-state.service';

@Component({
  selector: 'app-person-modal',
  imports: [NgIcon, HlmButtonImports, HlmBadgeImports, HlmDialogImports],
  providers: [
    provideIcons({
      lucideUser,
      lucideUserPlus,
      lucideUserCheck,
      lucideCheck,
      lucideTrash2,
      lucideZap,
      lucideTarget,
      lucideLoader2,
      lucidePlus,
      lucideBan,
      lucideMinus,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <!-- Rendered behind @if in the shell: the dialog is open for as long as the component exists -->
    <hlm-dialog state="open" (closed)="close.emit()">
      <hlm-dialog-content
        *hlmDialogPortal="let ctx"
        class="sm:max-w-xl w-full p-0 gap-0 flex flex-col max-h-[90vh] overflow-hidden rounded-2xl bg-card"
        closeLabel="Close"
      >
        <!-- Modal Header -->
        <hlm-dialog-header class="flex-row items-center gap-2.5 border-b border-border px-6 py-4 pr-14 bg-muted/20">
          <div class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ng-icon name="lucideTarget" class="size-4" />
          </div>
          <div class="flex flex-col gap-1">
            <h3 hlmDialogTitle class="font-sans font-bold text-base text-foreground">
              {{ frameId() ? 'Tag Face in Candidate Frame' : 'Person & Character Management' }}
            </h3>
            <p hlmDialogDescription class="text-xs text-muted-foreground">
              {{ frameId() ? 'Select or create a person identity to prioritize in extractions' : 'Manage target identities for auto-extraction and scoring' }}
            </p>
          </div>
        </hlm-dialog-header>

        <!-- Modal Body -->
        <div class="flex flex-col gap-5 p-6 overflow-y-auto">
          <!-- Video Context Indicator -->
          @if (currentVideo(); as v) {
            <div class="flex flex-col gap-2 p-3 rounded-xl bg-muted/40 border border-border/60 text-xs">
              <div class="flex items-center justify-between">
                <span class="text-muted-foreground font-semibold">Current Video:</span>
                <span class="font-bold text-foreground truncate max-w-xs">{{ v.name }}</span>
              </div>

              <!-- Positive & Negative Badges -->
              <div class="flex flex-wrap items-center gap-1.5 pt-1 border-t border-border/40">
                <span class="text-xs text-muted-foreground font-medium">Positive (Prioritize):</span>
                @for (pos of v.positive_persons || []; track pos.id) {
                  <span hlmBadge variant="secondary" class="gap-1 bg-primary/10 text-primary border-primary/20 text-xs">
                    <ng-icon name="lucideTarget" class="size-3" aria-hidden="true" />
                    {{ pos.name }}
                  </span>
                } @empty {
                  <span class="text-xs text-muted-foreground italic">None</span>
                }

                <span class="text-xs text-muted-foreground font-medium ml-2">Negative (Exclude):</span>
                @for (neg of v.negative_persons || []; track neg.id) {
                  <span hlmBadge variant="destructive" class="gap-1 bg-destructive/10 text-destructive border-destructive/20 text-xs">
                    <ng-icon name="lucideBan" class="size-3" aria-hidden="true" />
                    {{ neg.name }}
                  </span>
                } @empty {
                  <span class="text-xs text-muted-foreground italic">None</span>
                }

                @if ((v.positive_persons?.length || 0) > 0 || (v.negative_persons?.length || 0) > 0) {
                  <button
                    hlmBtn
                    variant="ghost"
                    size="xs"
                    (click)="onClearVideoPersons()"
                    class="h-6 text-xs text-muted-foreground hover:text-destructive cursor-pointer ml-auto"
                    title="Remove all person filters for this video"
                  >
                    Clear All
                  </button>
                }
              </div>

              <!-- Re-evaluate button on already extracted frames -->
              @if (v.status !== 'new') {
                <div class="flex items-center justify-between pt-1 border-t border-border/40 text-xs text-muted-foreground">
                  <span>Recognition runs locally on extracted candidate images</span>
                  <button
                    hlmBtn
                    variant="outline"
                    size="xs"
                    (click)="onReEvaluateFaces()"
                    [disabled]="isSubmitting()"
                    class="h-6 gap-1 cursor-pointer text-primary"
                    title="Re-run face recognition and positive/negative rules across all extracted images"
                  >
                    <ng-icon name="lucideZap" class="size-3" [class.animate-spin]="isSubmitting()" />
                    <span>Re-evaluate Extracted Images</span>
                  </button>
                </div>
              }
            </div>
          }

          <!-- New Person Input Form -->
          <div class="flex flex-col gap-2">
            <label class="text-xs font-semibold text-foreground">
              {{ frameId() ? 'Add New Person from this Frame' : 'Register New Person' }}
            </label>
            <div class="flex items-center gap-2">
              <input
                type="text"
                [value]="newPersonName()"
                (input)="newPersonName.set($any($event.target).value)"
                placeholder="Enter person name (e.g. Elena, Chloe)..."
                class="flex-1 h-9 rounded-lg border border-input bg-background px-3 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                (keydown.enter)="onCreateOrTagNew()"
              />
              <button
                hlmBtn
                variant="default"
                size="sm"
                [disabled]="!newPersonName().trim() || isSubmitting()"
                (click)="onCreateOrTagNew()"
                class="cursor-pointer gap-1.5 font-semibold shrink-0"
              >
                @if (isSubmitting()) {
                  <ng-icon name="lucideLoader2" class="size-3.5 animate-spin" />
                } @else {
                  <ng-icon name="lucidePlus" class="size-3.5" />
                }
                <span>{{ frameId() ? 'Tag Frame' : 'Create' }}</span>
              </button>
            </div>
          </div>

          <!-- Existing Persons Grid -->
          <div class="flex flex-col gap-2.5">
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Registered Persons ({{ persons().length }})
              </span>
              @if (personService.loading()) {
                <ng-icon name="lucideLoader2" class="size-3.5 animate-spin text-muted-foreground" />
              }
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-h-60 overflow-y-auto pr-1">
              @for (p of persons(); track p.id) {
                @let isCurrentTarget = currentVideo()?.target_person_id === p.id;
                <div
                  class="group flex items-center justify-between p-2.5 rounded-xl border transition-all text-xs"
                  [class.border-primary]="isCurrentTarget"
                  [class.bg-primary/5]="isCurrentTarget"
                  [class.border-border]="!isCurrentTarget"
                  [class.bg-card]="!isCurrentTarget"
                >
                  <!-- Person Avatar & Details -->
                  <div class="flex items-center gap-2.5 min-w-0">
                    @if (p.face_crop_url) {
                      <img
                        [src]="p.face_crop_url"
                        [alt]="p.name"
                        class="size-9 rounded-full object-cover border border-border shrink-0"
                      />
                    } @else {
                      <div class="size-9 rounded-full bg-secondary flex items-center justify-center text-secondary-foreground font-bold shrink-0">
                        {{ p.name.charAt(0).toUpperCase() }}
                      </div>
                    }

                    <div class="flex flex-col min-w-0">
                      <span class="font-bold text-foreground truncate" [title]="p.name">
                        {{ p.name }}
                      </span>
                      <span class="text-xs text-muted-foreground">
                        {{ p.exemplar_count }} {{ p.exemplar_count === 1 ? 'exemplar' : 'exemplars' }}
                      </span>
                    </div>
                  </div>

                  <!-- Actions -->
                  <div class="flex items-center gap-1 shrink-0">
                    @if (frameId()) {
                      <!-- Tag this frame with existing person -->
                      <button
                        hlmBtn
                        variant="secondary"
                        size="xs"
                        [disabled]="isSubmitting()"
                        (click)="onTagExisting(p)"
                        title="Add this frame as an exemplar for {{ p.name }}"
                        class="cursor-pointer gap-1"
                      >
                        <ng-icon name="lucideUserCheck" class="size-3 text-primary" />
                        <span>Tag</span>
                      </button>
                    } @else if (currentVideo()) {
                      @let isPos = isPersonPositive(p.id);
                      @let isNeg = isPersonNegative(p.id);

                      <!-- Toggle Positive Target -->
                      <button
                        hlmBtn
                        [variant]="isPos ? 'default' : 'outline'"
                        size="xs"
                        [disabled]="isSubmitting()"
                        (click)="onTogglePersonRole(p, 'positive')"
                        class="cursor-pointer gap-1 text-xs h-7 px-2"
                        title="{{ isPos ? 'Remove positive prioritization' : 'Prioritize frames with ' + p.name }}"
                      >
                        <ng-icon [name]="isPos ? 'lucideTarget' : 'lucidePlus'" class="size-3" aria-hidden="true" />
                        <span>{{ isPos ? 'Active' : 'Positive' }}</span>
                      </button>

                      <!-- Toggle Negative Exclusion -->
                      <button
                        hlmBtn
                        [variant]="isNeg ? 'destructive' : 'outline'"
                        size="xs"
                        [disabled]="isSubmitting()"
                        (click)="onTogglePersonRole(p, 'negative')"
                        class="cursor-pointer gap-1 text-xs h-7 px-2"
                        title="{{ isNeg ? 'Remove negative exclusion' : 'Exclude frames showing exclusively ' + p.name }}"
                      >
                        <ng-icon [name]="isNeg ? 'lucideBan' : 'lucideMinus'" class="size-3" aria-hidden="true" />
                        <span>{{ isNeg ? 'Excluded' : 'Negative' }}</span>
                      </button>
                    }

                    <button
                      hlmBtn
                      variant="ghost"
                      size="xs"
                      (click)="onDeletePerson(p)"
                      title="Delete person"
                      [attr.aria-label]="'Delete person ' + p.name"
                      class="size-7 p-0 text-muted-foreground hover:text-destructive cursor-pointer opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
                    >
                      <ng-icon name="lucideTrash2" class="size-3.5" />
                    </button>
                  </div>
                </div>
              } @empty {
                <div class="col-span-full py-8 text-center text-xs text-muted-foreground">
                  No persons registered yet. Create one above to begin face-targeted auto-extraction.
                </div>
              }
            </div>
          </div>

          <!-- Auto-Extraction Shortcut Action -->
          @if (!frameId() && currentVideo()) {
            <div class="flex items-center justify-between p-3.5 rounded-xl border border-primary/30 bg-primary/5">
              <div class="flex flex-col">
                <span class="font-bold text-xs text-foreground flex items-center gap-1.5">
                  <ng-icon name="lucideZap" class="size-3.5 text-primary" />
                  <span>Autonomous Extraction Pipeline</span>
                </span>
                <span class="text-xs text-muted-foreground">
                  Autonomously chunks, decodes, extracts candidate frames, and prioritizes the target person.
                </span>
              </div>
              <button
                hlmBtn
                variant="default"
                size="sm"
                [disabled]="isSubmitting()"
                (click)="onTriggerAutoExtract()"
                class="cursor-pointer gap-1.5 font-bold shadow-xs shrink-0"
              >
                <ng-icon name="lucideZap" class="size-3.5" />
                <span>Auto-Extract</span>
              </button>
            </div>
          }
        </div>

        <!-- Modal Footer -->
        <hlm-dialog-footer class="border-t border-border px-6 py-3 bg-muted/10">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="close.emit()"
            class="cursor-pointer"
          >
            Close
          </button>
        </hlm-dialog-footer>
      </hlm-dialog-content>
    </hlm-dialog>
  `,
})
export class PersonModalComponent implements OnInit {
  readonly personService = inject(PersonService);
  private readonly videoService = inject(VideoService);
  private readonly curateService = inject(CurateService);
  private readonly confirm = inject(ConfirmService);

  readonly close = output<void>();
  readonly frameId = input<number | null>(null);

  readonly newPersonName = signal<string>('');
  readonly isSubmitting = signal<boolean>(false);

  readonly persons = this.personService.persons;
  readonly currentVideo = this.videoService.currentVideo;

  constructor() {
    // Rendered behind @if in the shell: open for as long as it exists
    registerModal('person');
  }

  ngOnInit(): void {
    this.personService.loadPersons();
  }

  async onCreateOrTagNew(): Promise<void> {
    const name = this.newPersonName().trim();
    if (!name) return;

    this.isSubmitting.set(true);
    try {
      const v = this.currentVideo();
      const fId = this.frameId();

      if (v && fId !== null) {
        // Tag face from frame
        await this.personService.tagFaceFromFrame(v.id, fId, name);
        await this.videoService.loadVideos();
        await this.curateService.loadCandidates(v.id);
        this.newPersonName.set('');
        this.close.emit();
      } else {
        // Just create new person
        await this.personService.createPerson(name);
        this.newPersonName.set('');
      }
    } catch (err) {
      console.error('Failed to create/tag person:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onTagExisting(p: Person): Promise<void> {
    const v = this.currentVideo();
    const fId = this.frameId();
    if (!v || fId === null) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.tagFaceFromFrame(v.id, fId, p.name, p.id);
      await this.videoService.loadVideos();
      await this.curateService.loadCandidates(v.id);
      this.close.emit();
    } catch (err) {
      console.error('Failed to tag frame with existing person:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  isPersonPositive(personId: number): boolean {
    const v = this.currentVideo();
    if (!v) return false;
    if (v.positive_person_ids?.includes(personId)) return true;
    return v.target_person_id === personId;
  }

  isPersonNegative(personId: number): boolean {
    const v = this.currentVideo();
    if (!v) return false;
    return Boolean(v.negative_person_ids?.includes(personId));
  }

  async onTogglePersonRole(p: Person, targetRole: 'positive' | 'negative'): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    const currentlyActive = targetRole === 'positive' ? this.isPersonPositive(p.id) : this.isPersonNegative(p.id);
    const newRole = currentlyActive ? 'none' : targetRole;

    this.isSubmitting.set(true);
    try {
      await this.personService.setVideoPersonRole(v.id, p.id, newRole);
      await this.videoService.loadVideos();
      await this.curateService.loadCandidates(v.id);
    } catch (err) {
      console.error(`Failed to set person role to ${newRole}:`, err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onClearVideoPersons(): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.setVideoTargetPerson(v.id, null);
      await this.videoService.loadVideos();
      await this.curateService.loadCandidates(v.id);
    } catch (err) {
      console.error('Failed to clear video persons:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onReEvaluateFaces(): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.reEvaluateFaces(v.id);
      await this.videoService.loadVideos();
      await this.curateService.loadCandidates(v.id);
    } catch (err) {
      console.error('Failed to re-evaluate faces on extracted frames:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onSetVideoTarget(p: Person): Promise<void> {
    await this.onTogglePersonRole(p, 'positive');
  }

  async onClearVideoTarget(): Promise<void> {
    await this.onClearVideoPersons();
  }

  async onDeletePerson(p: Person): Promise<void> {
    const ok = await this.confirm.ask({
      title: `Delete ${p.name}?`,
      description: 'The person and all face exemplars are removed. Videos that target this person lose the filter. This cannot be undone.',
      confirmLabel: 'Delete person',
    });
    if (!ok) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.deletePerson(p.id);
      await this.videoService.loadVideos();
    } catch (err) {
      console.error('Failed to delete person:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onTriggerAutoExtract(): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.autoExtract(v.id, v.target_person_id);
      await this.videoService.loadVideos();
      this.close.emit();
    } catch (err) {
      console.error('Failed to trigger auto extract:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }
}
