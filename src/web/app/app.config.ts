import { ApplicationConfig, provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { provideSpartanHlm } from '@spartan-ng/helm/utils';
import { apiErrorInterceptor } from './core/services/api-error.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    // No zone.js: change detection runs off signals, template events and markForCheck only
    provideZonelessChangeDetection(),
    // ApiService goes through HttpClient; the interceptor maps every non-2xx response to ApiError
    provideHttpClient(withFetch(), withInterceptors([apiErrorInterceptor])),
    provideSpartanHlm(),
  ],
};
