// The sign-in token lives in this browser only. Storage can be unavailable
// (private mode, blocked site data), so every access is guarded.
const TOKEN_KEY = "job-search-token";

export function getToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string) {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Without storage the user stays signed in only until the page reloads.
  }
}

export function clearToken() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored, nothing to clear.
  }
}

export const PUBLIC_PATHS = ["/login", "/setup", "/join"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

export function redirectToLogin() {
  if (typeof window === "undefined" || isPublicPath(window.location.pathname)) {
    return;
  }
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}
