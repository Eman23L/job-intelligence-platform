"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AuthCard, NameEmailPassword } from "@/components/AuthCard";
import { authApi } from "@/lib/api";
import { setToken } from "@/lib/session";

export default function JoinPage() {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCode(new URLSearchParams(window.location.search).get("code") ?? "");
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await authApi.register({ invite_code: code, email, password, name });
      setToken(session.token);
      // New accounts start with no CV, so send them straight to add one.
      window.location.replace("/profile");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create your account. Try again.");
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title="Create your account"
      subtitle="You've been invited. Your CV, jobs and applications stay private to you."
      error={error}
      busy={busy}
      submitLabel="Create account"
      onSubmit={submit}
      footer={
        <>
          Already have an account? <Link href="/login">Sign in</Link>
        </>
      }
    >
      {!code ? (
        <label>
          Invite code
          <input value={code} onChange={(event) => setCode(event.target.value)} autoComplete="off" required />
        </label>
      ) : null}
      <NameEmailPassword name={name} email={email} password={password} onName={setName} onEmail={setEmail} onPassword={setPassword} />
    </AuthCard>
  );
}
