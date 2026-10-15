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
import { CommonModule } from '@angular/common';
import { NgIcon, provideIcons } from '@ng-icons/core';
import {
  lucideUser,
  lucideUserPlus,
  lucideUserCheck,
  lucideCheck,
  lucideX,
  lucideTrash2,
  lucideZap,
  lucideTarget,
  lucideLoader2,
  lucidePlus,
} from '@ng-icons/lucide';
import { HlmButtonImports } from '@spartan-ng/helm/button';
import { HlmBadgeImports } from '@spartan-ng/helm/badge';
import { PersonService } from '../../core/services/person.service';
import { VideoService } from '../../core/services/video.service';
import { CurateService } from '../../core/services/curate.service';
import { Person, DetectedFace } from '../../core/models/person.model';

@Component({
  selector: 'app-person-modal',
  imports: [CommonModule, NgIcon, HlmButtonImports, HlmBadgeImports],
  providers: [
    provideIcons({
      lucideUser,
      lucideUserPlus,
      lucideUserCheck,
      lucideCheck,
      lucideX,
      lucideTrash2,
      lucideZap,
      lucideTarget,
      lucideLoader2,
      lucidePlus,
    }),
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-xs p-4 animate-in fade-in select-none"
      role="dialog"
      aria-modal="true"
      aria-labelledby="person-modal-title"
      (click)="onBackdropClick($event)"
    >
      <div
        class="relative flex flex-col max-h-[90vh] max-w-xl w-full rounded-2xl border border-border bg-card overflow-hidden shadow-2xl"
        (click)="$event.stopPropagation()"
      >
        <!-- Modal Header -->
        <div class="flex items-center justify-between border-b border-border px-6 py-4 bg-muted/20">
          <div class="flex items-center gap-2.5">
            <div class="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <ng-icon name="lucideTarget" class="size-4" />
            </div>
            <div>
              <h3 id="person-modal-title" class="font-sans font-bold text-base text-foreground">
                {{ frameId() ? 'Tag Face in Candidate Frame' : 'Person & Character Management' }}
              </h3>
              <p class="text-xs text-muted-foreground">
                {{ frameId() ? 'Select or create a person identity to prioritize in extractions' : 'Manage target identities for auto-extraction and scoring' }}
              </p>
            </div>
          </div>
          <button
            hlmBtn
            variant="ghost"
            size="xs"
            (click)="close.emit()"
            class="size-8 rounded-full cursor-pointer"
            aria-label="Close"
          >
            <ng-icon name="lucideX" class="size-4" />
          </button>
        </div>

        <!-- Modal Body -->
        <div class="flex flex-col gap-5 p-6 overflow-y-auto">
          <!-- Video Context Indicator -->
          @if (currentVideo(); as v) {
            <div class="flex items-center justify-between p-3 rounded-xl bg-muted/40 border border-border/60 text-xs">
              <div class="flex flex-col">
                <span class="text-muted-foreground">Current Video:</span>
                <span class="font-bold text-foreground truncate max-w-xs">{{ v.name }}</span>
              </div>
              <div class="flex items-center gap-2">
                @if (v.target_person_name) {
                  <span hlmBadge variant="secondary" class="gap-1 bg-primary/10 text-primary border-primary/20">
                    🎯 {{ v.target_person_name }}
                  </span>
                  <button
                    hlmBtn
                    variant="ghost"
                    size="xs"
                    (click)="onClearVideoTarget()"
                    class="h-6 text-xs text-muted-foreground hover:text-destructive cursor-pointer"
                  >
                    Clear
                  </button>
                } @else {
                  <span class="text-muted-foreground italic">No target person set</span>
                }
              </div>
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
                      <span class="text-[11px] text-muted-foreground">
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
                    } @else {
                      <!-- Assign as video target -->
                      <button
                        hlmBtn
                        [variant]="isCurrentTarget ? 'default' : 'outline'"
                        size="xs"
                        [disabled]="isCurrentTarget || isSubmitting()"
                        (click)="onSetVideoTarget(p)"
                        class="cursor-pointer"
                        title="{{ isCurrentTarget ? 'Currently assigned' : 'Assign to current video' }}"
                      >
                        {{ isCurrentTarget ? 'Active' : 'Assign' }}
                      </button>
                    }

                    <button
                      hlmBtn
                      variant="ghost"
                      size="xs"
                      (click)="onDeletePerson(p)"
                      title="Delete person"
                      class="size-7 p-0 text-muted-foreground hover:text-destructive cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity"
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
                <span class="text-[11px] text-muted-foreground">
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
        <div class="flex items-center justify-end border-t border-border px-6 py-3 bg-muted/10">
          <button
            hlmBtn
            variant="outline"
            size="sm"
            (click)="close.emit()"
            class="cursor-pointer"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  `,
})
export class PersonModalComponent implements OnInit {
  readonly personService = inject(PersonService);
  private readonly videoService = inject(VideoService);
  private readonly curateService = inject(CurateService);

  readonly close = output<void>();
  readonly frameId = input<number | null>(null);

  readonly newPersonName = signal<string>('');
  readonly isSubmitting = signal<boolean>(false);

  readonly persons = this.personService.persons;
  readonly currentVideo = this.videoService.currentVideo;

  ngOnInit(): void {
    this.personService.loadPersons();
  }

  onBackdropClick(e: MouseEvent): void {
    if (e.target === e.currentTarget) {
      this.close.emit();
    }
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

  async onSetVideoTarget(p: Person): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.setVideoTargetPerson(v.id, p.id);
      await this.videoService.loadVideos();
    } catch (err) {
      console.error('Failed to set video target person:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onClearVideoTarget(): Promise<void> {
    const v = this.currentVideo();
    if (!v) return;

    this.isSubmitting.set(true);
    try {
      await this.personService.setVideoTargetPerson(v.id, null);
      await this.videoService.loadVideos();
    } catch (err) {
      console.error('Failed to clear video target person:', err);
    } finally {
      this.isSubmitting.set(false);
    }
  }

  async onDeletePerson(p: Person): Promise<void> {
    if (!confirm(`Are you sure you want to delete person "${p.name}"?`)) return;

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
