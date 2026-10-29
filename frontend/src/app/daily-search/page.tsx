"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { DiscoveryFeed, DiscoveryStatus } from "@/types/api";

const FEED_SIGNUP: Record<string, { url: string; label: string }> = {
  reed: { url: "https://www.reed.co.uk/developers/jobseeker", label: "reed.co.uk/developers" },
  adzuna: { url: "https://developer.adzuna.com/signup", label: "developer.adzuna.com" }
};

export default function DailySearchPage() {
  const [status, setStatus] = useState<DiscoveryStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => api.discoveryStatus().then(setStatus), []);

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [load]);

  const searchNow = async () => {
    setRunning(true);
    setNotice(null);
    try {
      await api.runDiscovery();
      setNotice({
        type: "success",
        message: "Searching now. New matches will appear on your Applications page in a few minutes."
      });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't start the search." });
    } finally {
      setRunning(false);
    }
  };

  const createKey = async () => {
    if (status?.import_key.exists && !window.confirm("Make a new key? The old one will stop working.")) {
      return;
    }
    setKeyBusy(true);
    try {
      const created = await api.createImportKey();
      setNewKey(created.key);
      setCopied(false);
      await load();
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't create a key." });
    } finally {
      setKeyBusy(false);
    }
  };

  const revokeKey = async () => {
    if (!window.confirm("Turn off the daily LinkedIn and Indeed check? Its key will stop working.")) {
      return;
    }
    setKeyBusy(true);
    try {
      await api.deleteImportKey();
      setNewKey(null);
      await load();
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't remove the key." });
    } finally {
      setKeyBusy(false);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!status) {
    return <LoadingState label="Loading your daily search" />;
  }

  return (
    <div className="page-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>How it works</h2>
            <p className="muted">
              Every weekday at 6:30am the app searches for new jobs, scores each one against your CV, and puts those scoring{" "}
              {status.minimum_score} or more into <Link href="/applications">New matches</Link>. Approve the ones you want, then Claude helps
              you apply.
            </p>
          </div>
          <button type="button" disabled={running || status.roles.length === 0} onClick={() => void searchNow()}>
            {running ? <span className="spinner" aria-hidden="true" /> : null}
            Search now
          </button>
        </div>
        {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
        {status.roles.length ? (
          <dl className="detail-list">
            <div className="detail-row">
              <dt>Searching for</dt>
              <dd>{status.roles.join(", ")}</dd>
            </div>
            <div className="detail-row">
              <dt>Near</dt>
              <dd>{status.location ?? "Anywhere in the UK"}</dd>
            </div>
            <div className="detail-row">
              <dt>Next search</dt>
              <dd>{status.next_run_at ? formatWhen(status.next_run_at) : "When the background worker is running"}</dd>
            </div>
          </dl>
        ) : (
          <div className="notice-banner warning">
            Add your CV on the <Link href="/profile">Profile</Link> page so the search knows which roles to look for.
          </div>
        )}
        <p className="muted-text">
          The roles come from the TARGET ROLES line of your CV text on the <Link href="/profile">Profile</Link> page, and the score from your
          minimum apply score there.
        </p>
      </section>

      <section className="panel">
        <h2>Job boards</h2>
        <div className="feed-list">
          {status.feeds.map((feed) => (
            <FeedRow key={feed.key} feed={feed} />
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>LinkedIn and Indeed</h2>
        <p className="muted">
          LinkedIn and Indeed don&apos;t allow automated searching, so this uses the job alert emails they already send you. Each weekday
          morning, Claude reads your new alerts in Gmail, picks the jobs that fit your CV and adds them to New matches with a one-line reason.
        </p>
        <ol className="steps">
          <li>
            On LinkedIn, search for a role you want, then click <strong>Set alert</strong> and choose daily email alerts. On Indeed, search and
            choose <strong>Get new jobs for this search by email</strong>. Do this for each of your target roles.
          </li>
          <li>Create your key below. It can only add jobs to your New matches, nothing else.</li>
          <li>Send the key to Claude in your chat and ask it to switch on the daily LinkedIn and Indeed check.</li>
        </ol>
        {newKey ? (
          <div className="page-stack">
            <div className="key-box">{newKey}</div>
            <div className="action-row">
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(newKey)
                    .then(() => setCopied(true))
                    .catch(() => setCopied(false));
                }}
              >
                {copied ? "Copied" : "Copy key"}
              </button>
              <span className="muted-text">This is the only time it&apos;s shown. Keep it private.</span>
            </div>
          </div>
        ) : null}
        <div className="action-row">
          <button type="button" className={status.import_key.exists ? "secondary-button" : undefined} disabled={keyBusy} onClick={() => void createKey()}>
            {status.import_key.exists ? "Make a new key" : "Create my key"}
          </button>
          {status.import_key.exists ? (
            <>
              <span className="muted-text">
                Key created {formatDate(status.import_key.created_at)}.{" "}
                {status.import_key.last_used_at ? `Last used ${formatWhen(status.import_key.last_used_at)}.` : "Not used yet."}
              </span>
              <button type="button" className="ghost-button" disabled={keyBusy} onClick={() => void revokeKey()}>
                Turn off
              </button>
            </>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function FeedRow({ feed }: { feed: DiscoveryFeed }) {
  const signup = FEED_SIGNUP[feed.key];
  return (
    <div className="feed-row">
      <strong>{feed.name}</strong>
      {feed.enabled ? (
        <span className="badge good">On</span>
      ) : (
        <span className="badge neutral">Needs a free API key</span>
      )}
      <span className="muted-text">
        {feed.enabled
          ? feed.last_run_at
            ? `Last searched ${formatWhen(feed.last_run_at)}: ${feed.last_found} found, ${feed.last_created} new${feed.last_status === "failed" ? " (had errors)" : ""}`
            : "Not searched yet"
          : signup
            ? (
                <>
                  Get one free at{" "}
                  <a href={signup.url} target="_blank" rel="noreferrer">
                    {signup.label} ↗
                  </a>{" "}
                  and send it to Claude in your chat to switch this on.
                </>
              )
            : null}
      </span>
    </div>
  );
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}
