import Link from "next/link";
import type { FormEvent, ReactNode } from "react";

export function AuthCard({
  title,
  subtitle,
  error,
  busy,
  submitLabel,
  onSubmit,
  children,
  footer
}: {
  title: string;
  subtitle?: string;
  error: string | null;
  busy: boolean;
  submitLabel: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="auth-card">
      <Link href="/login" className="brand auth-brand">
        <span className="brand-mark">JS</span>
        <span>
          <strong>Job Search</strong>
          <small>Find roles that fit you</small>
        </span>
      </Link>
      <h1>{title}</h1>
      {subtitle ? <p className="muted">{subtitle}</p> : null}
      <form className="auth-form" onSubmit={onSubmit}>
        {children}
        {error ? (
          <div className="notice-banner error" role="alert">
            {error}
          </div>
        ) : null}
        <button type="submit" disabled={busy}>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          {busy ? "Please wait..." : submitLabel}
        </button>
      </form>
      {footer ? <div className="auth-footer">{footer}</div> : null}
    </div>
  );
}

export function nextPathFromLocation(fallback: string): string {
  if (typeof window === "undefined") {
    return fallback;
  }
  const next = new URLSearchParams(window.location.search).get("next");
  // Only follow same-site paths so the link can't bounce users to another website.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}

export function NameEmailPassword({
  name,
  email,
  password,
  onName,
  onEmail,
  onPassword
}: {
  name: string;
  email: string;
  password: string;
  onName: (value: string) => void;
  onEmail: (value: string) => void;
  onPassword: (value: string) => void;
}) {
  return (
    <>
      <label>
        Your name
        <input autoComplete="name" value={name} onChange={(event) => onName(event.target.value)} required />
      </label>
      <label>
        Email
        <input type="email" autoComplete="email" value={email} onChange={(event) => onEmail(event.target.value)} required />
      </label>
      <label>
        Password
        <input
          type="password"
          autoComplete="new-password"
          minLength={8}
          value={password}
          onChange={(event) => onPassword(event.target.value)}
          required
        />
        <span className="muted-text">At least 8 characters.</span>
      </label>
    </>
  );
}
