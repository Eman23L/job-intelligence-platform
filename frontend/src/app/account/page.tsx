"use client";

import { useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { authApi, type Account } from "@/lib/api";
import { formatDate } from "@/lib/format";

export default function AccountPage() {
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [invite, setInvite] = useState<{ link: string; expires: string } | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    authApi
      .me()
      .then(setAccount)
      .catch((err: Error) => setError(err.message));
  }, []);

  const createInvite = async () => {
    setCreating(true);
    setInviteError(null);
    setCopied(false);
    try {
      const created = await authApi.createInvite(note.trim() || undefined);
      setInvite({ link: `${window.location.origin}/join?code=${encodeURIComponent(created.code)}`, expires: created.expires_at });
      setNote("");
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Couldn't create an invite.");
    } finally {
      setCreating(false);
    }
  };

  const copy = async () => {
    if (!invite) {
      return;
    }
    try {
      await navigator.clipboard.writeText(invite.link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!account) {
    return <LoadingState label="Loading account" />;
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <h2>You</h2>
        <div className="metric-list">
          <div className="metric-row">
            <span className="muted">Name</span>
            <strong>{account.display_name ?? "Not set"}</strong>
          </div>
          <div className="metric-row">
            <span className="muted">Email</span>
            <strong>{account.email}</strong>
          </div>
          <div className="metric-row">
            <span className="muted">Role</span>
            <strong>{account.is_admin ? "Owner" : "Member"}</strong>
          </div>
        </div>
      </section>

      {account.is_admin ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Invite someone</h2>
              <p className="muted-text">
                Each link creates one account and works once. Their CV, jobs and applications are kept separate from yours.
              </p>
            </div>
          </div>
          <div className="action-row">
            <label style={{ flex: "1 1 240px" }}>
              Who is it for? (optional, for your reference)
              <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="e.g. Sam" />
            </label>
            <button type="button" style={{ alignSelf: "flex-end" }} disabled={creating} onClick={() => void createInvite()}>
              {creating ? "Creating..." : "Create invite link"}
            </button>
          </div>
          {inviteError ? <div className="notice-banner error">{inviteError}</div> : null}
          {invite ? (
            <div className="invite-result">
              <p className="muted-text">Send this link to them. It expires on {formatDate(invite.expires)} and won&apos;t be shown again.</p>
              <div className="chat-input-row">
                <input readOnly value={invite.link} onFocus={(event) => event.currentTarget.select()} aria-label="Invite link" />
                <button type="button" className="secondary-button" onClick={() => void copy()}>
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
