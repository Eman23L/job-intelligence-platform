"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AuthCard, nextPathFromLocation } from "@/components/AuthCard";
import { authApi } from "@/lib/api";
import { getToken, setToken } from "@/lib/session";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [setupRequired, setSetupRequired] = useState(false);

  useEffect(() => {
    if (getToken()) {
      window.location.replace(nextPathFromLocation("/dashboard"));
      return;
    }
    authApi
      .status()
      .then((status) => setSetupRequired(status.setup_required))
      .catch(() => undefined);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await authApi.login(email, password);
      setToken(session.token);
      window.location.replace(nextPathFromLocation("/dashboard"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title="Sign in"
      error={error}
      busy={busy}
      submitLabel="Sign in"
      onSubmit={submit}
      footer={
        setupRequired ? (
          <>
            First time here? <Link href="/setup">Set up the owner account</Link>
          </>
        ) : (
          <>Got an invite link? Open it to create your account.</>
        )
      }
    >
      <label>
        Email
        <input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
      </label>
      <label>
        Password
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>
    </AuthCard>
  );
}
