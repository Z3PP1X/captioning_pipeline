/** Non-2xx response. `detail` is FastAPI's `detail` (a string, or an object such as the 409 re-chunk guard). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
