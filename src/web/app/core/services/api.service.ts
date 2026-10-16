import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { toApiError } from './api-error.interceptor';

export { ApiError } from './api-error';

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

/**
 * Promise-based JSON client over HttpClient. Errors are ApiError (mapped centrally by `apiErrorInterceptor`;
 * mapped here as well so the service behaves the same when HttpClient is provided without the interceptor).
 */
@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly headers = new HttpHeaders({ 'Content-Type': 'application/json' });

  async request<T>(method: Method, path: string, body?: unknown): Promise<T> {
    try {
      return await firstValueFrom(
        this.http.request<T>(method, path, {
          // Serialised here (not by HttpClient, which would send a bare string body unquoted)
          body: body !== undefined ? JSON.stringify(body) : null,
          headers: this.headers,
          responseType: 'json',
        }),
      );
    } catch (err) {
      throw toApiError(err);
    }
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>('GET', path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('POST', path, body);
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PUT', path, body);
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PATCH', path, body);
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>('DELETE', path);
  }
}
