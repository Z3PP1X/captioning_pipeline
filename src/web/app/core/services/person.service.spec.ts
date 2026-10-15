import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { PersonService } from './person.service';
import { ApiService } from './api.service';
import { Person } from '../models/person.model';

describe('PersonService', () => {
  let service: PersonService;
  let mockApi: Partial<Record<keyof ApiService, ReturnType<typeof vi.fn>>>;

  const mockPersons: Person[] = [
    {
      id: 1,
      name: 'Elena Fisher',
      slug: 'elena_fisher',
      exemplar_count: 3,
      has_embedding: true,
      face_crop_url: '/api/persons/1/crop',
      video_count: 2,
    },
    {
      id: 2,
      name: 'Chloe Frazer',
      slug: 'chloe_frazer',
      exemplar_count: 1,
      has_embedding: true,
      face_crop_url: '/api/persons/2/crop',
      video_count: 1,
    },
  ];

  beforeEach(() => {
    mockApi = {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        PersonService,
        { provide: ApiService, useValue: mockApi },
      ],
    });

    service = TestBed.inject(PersonService);
  });

  it('initializes with empty persons and not loading', () => {
    expect(service.persons()).toEqual([]);
    expect(service.loading()).toBe(false);
  });

  it('loads persons list via API', async () => {
    mockApi.get!.mockResolvedValueOnce({ persons: mockPersons, total: 2 });

    const persons = await service.loadPersons();

    expect(mockApi.get).toHaveBeenCalledWith('/api/persons');
    expect(persons).toEqual(mockPersons);
    expect(service.persons()).toEqual(mockPersons);
    expect(service.loading()).toBe(false);
  });

  it('creates new person and reloads list', async () => {
    const newPerson: Person = { id: 3, name: 'Nadine Ross', slug: 'nadine_ross', exemplar_count: 0 };
    mockApi.post!.mockResolvedValueOnce({ person: newPerson });
    mockApi.get!.mockResolvedValueOnce({ persons: [...mockPersons, newPerson], total: 3 });

    const created = await service.createPerson('Nadine Ross');

    expect(mockApi.post).toHaveBeenCalledWith('/api/persons', { name: 'Nadine Ross' });
    expect(created.name).toBe('Nadine Ross');
    expect(service.persons().length).toBe(3);
  });

  it('sets video target person', async () => {
    mockApi.post!.mockResolvedValueOnce({
      video: { id: 'vid-123', target_person_id: 1, target_person_name: 'Elena Fisher' },
    });

    const res = await service.setVideoTargetPerson('vid-123', 1);

    expect(mockApi.post).toHaveBeenCalledWith('/api/videos/vid-123/target-person', { person_id: 1 });
    expect(res.target_person_id).toBe(1);
  });

  it('tags face from frame and updates list', async () => {
    mockApi.post!.mockResolvedValueOnce({
      status: 'ok',
      person: mockPersons[0],
      updated_frames: 15,
    });
    mockApi.get!.mockResolvedValueOnce({ persons: mockPersons, total: 2 });

    const res = await service.tagFaceFromFrame('vid-123', 42, 'Elena Fisher', 1, 0);

    expect(mockApi.post).toHaveBeenCalledWith('/api/videos/vid-123/tag-face-from-frame', {
      frame_id: 42,
      person_name: 'Elena Fisher',
      person_id: 1,
      face_index: 0,
    });
    expect(res.status).toBe('ok');
    expect(res.updated_frames).toBe(15);
  });

  it('deletes person and updates list', async () => {
    mockApi.delete!.mockResolvedValueOnce({ status: 'ok', deleted: true });
    mockApi.get!.mockResolvedValueOnce({ persons: [mockPersons[1]], total: 1 });

    const deleted = await service.deletePerson(1);

    expect(mockApi.delete).toHaveBeenCalledWith('/api/persons/1');
    expect(deleted).toBe(true);
    expect(service.persons().length).toBe(1);
  });

  it('submits auto-extract job', async () => {
    mockApi.post!.mockResolvedValueOnce({ job_id: 99, status: 'queued' });

    const res = await service.autoExtract('vid-123', 1);

    expect(mockApi.post).toHaveBeenCalledWith('/api/videos/vid-123/auto-extract', {
      target_person_id: 1,
    });
    expect(res.job_id).toBe(99);
  });
});
