import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';
import { ApiError } from './api-error';

/**
 * Maps a failed HttpClient call to ApiError. `detail` is FastAPI's `detail` (a string, or an object such as
 * `{message, errors}` from the 409 re-chunk guard); without one it falls back to the status text. Network
 * failures (status 0) become ApiError(0) as well, so callers only ever see one error type.
 */
export function toApiError(err: unknown): unknown {
  if (err instanceof ApiError || !(err instanceof HttpErrorResponse)) return err;
  if (err.status === 0) {
    // HttpClient reports "Unknown Error" here; the request never reached FastAPI
    return new ApiError(0, null, 'Network error: the server could not be reached');
  }
  const body = err.error as { detail?: unknown } | string | null | undefined;
  // Non-JSON error bodies arrive as a string, network failures as a ProgressEvent: neither has `detail`
  const bodyDetail = body && typeof body === 'object' && 'detail' in body ? body.detail : undefined;
  const detail: unknown = bodyDetail ? bodyDetail : err.statusText;
  const message =
    typeof detail === 'string' ? detail : String((detail as { message?: string } | null)?.message ?? '');
  return new ApiError(err.status, detail, message || `API request failed with status ${err.status}`);
}

/** Central error mapping for every HttpClient request (registered in app.config). */
export const apiErrorInterceptor: HttpInterceptorFn = (req, next) =>
  next(req).pipe(catchError((err: unknown) => throwError(() => toApiError(err))));
