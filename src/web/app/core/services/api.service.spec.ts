import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ApiError, ApiService } from './api.service';
import { apiErrorInterceptor } from './api-error.interceptor';

describe('ApiService (HttpClient + apiErrorInterceptor)', () => {
  let api: ApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([apiErrorInterceptor])), provideHttpClientTesting()],
    });
    api = TestBed.inject(ApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /** Lets firstValueFrom subscribe before the request is looked up. */
  const settle = () => Promise.resolve();

  it('GETs JSON with the JSON content type and resolves the parsed body', async () => {
    const res = api.get<{ videos: number[] }>('/api/videos');
    await settle();
    const req = http.expectOne('/api/videos');
    expect(req.request.method).toBe('GET');
    expect(req.request.body).toBeNull();
    expect(req.request.headers.get('Content-Type')).toBe('application/json');
    req.flush({ videos: [1, 2] });
    await expect(res).resolves.toEqual({ videos: [1, 2] });
  });

  it('serialises bodies with JSON.stringify for POST/PUT/PATCH (strings stay quoted)', async () => {
    const post = api.post('/api/a', { k: 5 });
    const put = api.put('/api/b', 'raw');
    const patch = api.patch('/api/c', [1]);
    const del = api.delete('/api/d');
    await settle();

    const p = http.expectOne('/api/a');
    expect(p.request.method).toBe('POST');
    expect(p.request.body).toBe('{"k":5}');
    const u = http.expectOne('/api/b');
    expect(u.request.method).toBe('PUT');
    expect(u.request.body).toBe('"raw"');
    const c = http.expectOne('/api/c');
    expect(c.request.method).toBe('PATCH');
    expect(c.request.body).toBe('[1]');
    const d = http.expectOne('/api/d');
    expect(d.request.method).toBe('DELETE');

    [p, u, c, d].forEach((r) => r.flush({ ok: true }));
    await Promise.all([post, put, patch, del]);
  });

  it('POST without a body sends none', async () => {
    const res = api.post('/api/videos/1/extract');
    await settle();
    const req = http.expectOne('/api/videos/1/extract');
    expect(req.request.body).toBeNull();
    req.flush({});
    await res;
  });

  it('maps a FastAPI string detail to ApiError(status, detail, message)', async () => {
    const res = api.get('/api/videos/x');
    await settle();
    http.expectOne('/api/videos/x').flush({ detail: 'Video not found' }, { status: 404, statusText: 'Not Found' });
    const err = await res.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, detail: 'Video not found', message: 'Video not found' });
  });

  it('keeps an object detail ({message, errors}) and uses its message', async () => {
    const detail = { message: 'Chunks hold active curation picks.', errors: [{ chunk: 2 }] };
    const res = api.put('/api/videos/1/chunks', { chunks: [] });
    await settle();
    http.expectOne('/api/videos/1/chunks').flush({ detail }, { status: 409, statusText: 'Conflict' });
    const err = (await res.catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.detail).toEqual(detail);
    expect(err.message).toBe('Chunks hold active curation picks.');
  });

  it('falls back to the status text when the body has no detail, then to a generic message', async () => {
    const a = api.get('/api/a');
    const b = api.get('/api/b');
    await settle();
    http.expectOne('/api/a').flush('<html>boom</html>', { status: 500, statusText: 'Internal Server Error' });
    http.expectOne('/api/b').flush({ detail: { errors: [] } }, { status: 422, statusText: 'Unprocessable' });

    const errA = (await a.catch((e: unknown) => e)) as ApiError;
    expect(errA).toMatchObject({ status: 500, detail: 'Internal Server Error', message: 'Internal Server Error' });
    const errB = (await b.catch((e: unknown) => e)) as ApiError;
    expect(errB.detail).toEqual({ errors: [] });
    expect(errB.message).toBe('API request failed with status 422');
  });

  it('maps network failures to ApiError(0)', async () => {
    const res = api.get('/api/events-down');
    await settle();
    http.expectOne('/api/events-down').error(new ProgressEvent('error'), { status: 0, statusText: '' });
    const err = (await res.catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toContain('Network error');
  });

  it('the interceptor maps errors of direct HttpClient calls too', async () => {
    const client = TestBed.inject(HttpClient);
    const res = firstValueFrom(client.get('/api/direct'));
    http.expectOne('/api/direct').flush({ detail: 'nope' }, { status: 400, statusText: 'Bad Request' });
    const err = await res.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('nope');
  });
});
