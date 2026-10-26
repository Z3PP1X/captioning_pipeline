import { Routes } from '@angular/router';
import { Step1IngestComponent } from './pages/step1-ingest/step1-ingest.component';
import { Step2TriageComponent } from './pages/step2-triage/step2-triage.component';
import { Step3TaggingComponent } from './pages/step3-tagging/step3-tagging.component';
import { Step4CaptionComponent } from './pages/step4-caption/step4-caption.component';
import { Step5ExportRagComponent } from './pages/step5-export-rag/step5-export-rag.component';

export const STUDIO_ROUTES: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'ingest' },
  { path: 'ingest', component: Step1IngestComponent, title: 'Step 1: Dual-Path Ingest' },
  { path: 'triage', component: Step2TriageComponent, title: 'Step 2: HITL Triage & Clusters' },
  { path: 'tagging', component: Step3TaggingComponent, title: 'Step 3: Tagging & Stratification' },
  { path: 'captioning', component: Step4CaptionComponent, title: 'Step 4: OpenRouter Caption Studio' },
  { path: 'export-rag', component: Step5ExportRagComponent, title: 'Step 5: Export & LoRA Prompt Studio' },
];
