import { Injectable } from '@angular/core';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string> || {}),
    };

    const res = await fetch(path, {
      ...options,
      headers,
    });

    if (!res.ok) {
      let detail = res.statusText;
      try {
        const errJson = await res.json();
        if (errJson && errJson.detail) {
          detail = errJson.detail;
        }
      } catch {
        // Fallback to statusText
      }
      throw new Error(detail || `API request failed with status ${res.status}`);
    }

    return res.json() as Promise<T>;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'GET' });
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'DELETE' });
  }
}
