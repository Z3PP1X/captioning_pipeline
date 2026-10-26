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
  const detail = errorDetail(err);
  const message = detailMessage(detail);
  return new ApiError(err.status, detail, message || `API request failed with status ${err.status}`);
}

/** FastAPI's `detail` from the error body, else the status text. */
function errorDetail(err: HttpErrorResponse): unknown {
  const body = err.error as { detail?: unknown } | string | null | undefined;
  // Non-JSON error bodies arrive as a string, network failures as a ProgressEvent: neither has `detail`
  const bodyDetail = body && typeof body === 'object' && 'detail' in body ? body.detail : undefined;
  return bodyDetail ? bodyDetail : err.statusText;
}

function detailMessage(detail: unknown): string {
  return typeof detail === 'string' ? detail : String((detail as { message?: string } | null)?.message ?? '');
}

/** Central error mapping for every HttpClient request (registered in app.config). */
export const apiErrorInterceptor: HttpInterceptorFn = (req, next) =>
  next(req).pipe(catchError((err: unknown) => throwError(() => toApiError(err))));
