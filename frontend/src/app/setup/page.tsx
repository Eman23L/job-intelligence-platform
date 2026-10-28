"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { AuthCard, NameEmailPassword } from "@/components/AuthCard";
import { authApi } from "@/lib/api";
import { setToken } from "@/lib/session";

export default function SetupPage() {
  const [setupCode, setSetupCode] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await authApi.setup({ setup_code: setupCode, email, password, name });
      setToken(session.token);
      window.location.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Setup failed. Try again.");
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title="Set up the owner account"
      subtitle="This keeps your existing CV, jobs and saved roles, and makes you the account that can invite others."
      error={error}
      busy={busy}
      submitLabel="Create owner account"
      onSubmit={submit}
      footer={
        <>
          Already set up? <Link href="/login">Sign in</Link>
        </>
      }
    >
      <label>
        Setup code
        <input value={setupCode} onChange={(event) => setSetupCode(event.target.value)} autoComplete="off" required />
      </label>
      <NameEmailPassword name={name} email={email} password={password} onName={setName} onEmail={setEmail} onPassword={setPassword} />
    </AuthCard>
  );
}
