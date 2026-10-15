import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from './api.service';
import {
  FrameFacesResponse,
  Person,
  TagFaceRequest,
  TagFaceResponse,
} from '../models/person.model';
import { Video } from '../models/video.model';

@Injectable({
  providedIn: 'root',
})
export class PersonService {
  private readonly api = inject(ApiService);

  readonly persons = signal<Person[]>([]);
  readonly loading = signal<boolean>(false);

  async loadPersons(): Promise<Person[]> {
    this.loading.set(true);
    try {
      const res = await this.api.get<{ persons: Person[]; total: number }>('/api/persons');
      this.persons.set(res.persons || []);
      return res.persons || [];
    } finally {
      this.loading.set(false);
    }
  }

  async createPerson(name: string): Promise<Person> {
    const res = await this.api.post<{ person: Person }>('/api/persons', { name });
    await this.loadPersons();
    return res.person;
  }

  async patchPerson(id: number, name: string): Promise<Person> {
    const res = await this.api.patch<{ person: Person }>(`/api/persons/${id}`, { name });
    await this.loadPersons();
    return res.person;
  }

  async deletePerson(id: number): Promise<boolean> {
    const res = await this.api.delete<{ status: string; deleted: boolean }>(`/api/persons/${id}`);
    await this.loadPersons();
    return res.deleted;
  }

  async setVideoTargetPerson(videoId: string, personId: number | null): Promise<Video> {
    const res = await this.api.post<{ video: Video }>(`/api/videos/${videoId}/target-person`, {
      person_id: personId,
    });
    return res.video;
  }

  async getVideoPersons(videoId: string): Promise<{ positive: Person[]; negative: Person[] }> {
    return this.api.get<{ video_id: string; positive: Person[]; negative: Person[] }>(`/api/videos/${videoId}/persons`);
  }

  async setVideoPersonRole(videoId: string, personId: number, role: 'positive' | 'negative' | 'none'): Promise<any> {
    return this.api.post<any>(`/api/videos/${videoId}/persons/${personId}`, { role });
  }

  async reEvaluateFaces(videoId: string): Promise<any> {
    return this.api.post<any>(`/api/videos/${videoId}/re-evaluate-faces`, {});
  }

  async tagFaceFromFrame(
    videoId: string,
    frameId: number,
    personName?: string,
    personId?: number,
    faceIndex = 0,
  ): Promise<TagFaceResponse> {
    const body: TagFaceRequest = {
      frame_id: frameId,
      person_name: personName,
      person_id: personId,
      face_index: faceIndex,
    };
    const res = await this.api.post<TagFaceResponse>(`/api/videos/${videoId}/tag-face-from-frame`, body);
    await this.loadPersons();
    return res;
  }

  async getFrameFaces(frameId: number): Promise<FrameFacesResponse> {
    return this.api.get<FrameFacesResponse>(`/api/frames/${frameId}/faces`);
  }

  async autoExtract(videoId: string, targetPersonId?: number | null): Promise<{ job_id: number; status: string }> {
    return this.api.post<{ job_id: number; status: string }>(`/api/videos/${videoId}/auto-extract`, {
      target_person_id: targetPersonId ?? null,
    });
  }
}
