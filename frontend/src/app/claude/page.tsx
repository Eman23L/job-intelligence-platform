"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CandidateDetails, CvPanel, type Candidate } from "@/components/CandidateDetails";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { ScorePill } from "@/components/ScoreBadge";
import { api } from "@/lib/api";
import { SOURCE_LABELS } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import type { Application, ApplicationSource, FindResult, FoundJob, UserProfile } from "@/types/api";

const SUBMITTED = ["applied", "interviewing", "offer", "rejected"];
const RECENT_DAYS = 7;

// The page Claude in Chrome works from during the daily run: who you are, what to look for,
// today's JobServe jobs, and where to record each application.
export default function ClaudePage() {
  const [found, setFound] = useState<FindResult | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [applications, setApplications] = useState<Application[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [busyJob, setBusyJob] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [nextFound, nextProfile, nextApplications] = await Promise.all([api.find(), api.profile(), api.applications()]);
    setFound(nextFound);
    setProfile(nextProfile);
    setApplications(nextApplications.items);
  }, []);

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [load]);

  const autoScore = profile?.auto_apply_min_score ?? null;
  const todaysJobs = useMemo(() => (found?.jobs ?? []).filter((job) => !job.status || job.status === "to_apply"), [found]);
  const recent = useMemo(() => {
    const since = Date.now() - RECENT_DAYS * 24 * 60 * 60 * 1000;
    return applications
      .filter((item) => SUBMITTED.includes(item.status) && item.applied_at && new Date(item.applied_at).getTime() >= since)
      .sort((a, b) => (b.applied_at ?? "").localeCompare(a.applied_at ?? ""));
  }, [applications]);

  const record = async (job: FoundJob, applied: boolean) => {
    setBusyJob(job.job_id);
    setNotice(null);
    try {
      if (applied) {
        await api.createApplication({ job_id: job.job_id, status: "applied" });
      } else {
        await api.dismissJobs([job.job_id]);
      }
      await load();
      window.dispatchEvent(new Event("applications-changed"));
      setNotice({ type: "success", message: `${applied ? "Recorded as applied" : "Skipped"}: ${job.title}.` });
    } catch (err) {
      setNotice({ type: "error", message: err instanceof Error ? err.message : "Couldn't record that." });
    } finally {
      setBusyJob(null);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!found) {
    return <LoadingState label="Loading today's run" />;
  }
  if (!profile || !found.has_cv) {
    return (
      <section className="panel">
        <h2>Add your CV first</h2>
        <p className="muted-text">Claude needs your CV and details to apply for you.</p>
        <Link href="/profile" className="button-link">
          Go to My CV
        </Link>
      </section>
    );
  }

  const candidate: Candidate = {
    first_name: profile.first_name,
    last_name: profile.last_name,
    email: profile.email,
    phone: profile.phone,
    address: profile.address,
    country: profile.country,
    work_status_uk: profile.work_status_uk,
    sponsorship_required: profile.sponsorship_required,
    availability_notice: profile.availability_notice,
    salary_expectation: profile.salary_expectation,
    travel_distance: profile.travel_distance,
    cv_file_name: profile.cv_file_name,
    cv_text: profile.cv_text
  };
  const { search } = found;

  return (
    <div className="page-stack">
      <section className="panel">
        <h2>The jobs I want</h2>
        <dl className="detail-list">
          <div className="detail-row">
            <dt>Roles</dt>
            <dd>{search.roles.join(", ") || "Not chosen yet"}</dd>
          </div>
          <div className="detail-row">
            <dt>Where</dt>
            <dd>{search.remote_only ? "Remote only" : (search.location ?? "Anywhere in the UK")}</dd>
          </div>
          <div className="detail-row">
            <dt>JobServe</dt>
            <dd>
              {autoScore === null
                ? "Ask me before applying to any JobServe job."
                : `Apply without asking to JobServe jobs matching ${autoScore} or more. Ask me about the rest.`}
            </dd>
          </div>
        </dl>
        <p className="muted-text">
          Change these on <Link href="/profile#wanted">My CV</Link>.
        </p>
      </section>

      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}

      <section className="panel">
        <h2>Today&apos;s jobs</h2>
        <p className="muted-text">From my searches, best match first. Jobs I queued myself are already approved.</p>
        {todaysJobs.length === 0 ? (
          <p className="muted-text">No jobs waiting. The search runs every weekday at 6:30am.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table stack-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Match</th>
                  <th>Claude should</th>
                  <th aria-label="Record" />
                </tr>
              </thead>
              <tbody>
                {todaysJobs.map((job) => {
                  // Jobs I queued myself are already approved; JobServe jobs above my chosen score don't need asking.
                  const automatic =
                    job.status === "to_apply" ||
                    (job.source === "jobserve" && autoScore !== null && Number(job.total_score ?? 0) >= autoScore);
                  return (
                    <tr key={job.job_id}>
                      <td data-label="Job">
                        <div className="job-cell">
                          {job.url ? (
                            <a href={job.url} target="_blank" rel="noreferrer" className="table-link">
                              {job.title} ↗
                            </a>
                          ) : (
                            <strong>{job.title}</strong>
                          )}
                          <span className="job-meta">
                            {[job.company, job.location, job.salary, SOURCE_LABELS[job.source] ?? null].filter(Boolean).join(" · ")}
                          </span>
                        </div>
                      </td>
                      <td data-label="Match">
                        {job.total_score !== null ? <ScorePill score={job.total_score} /> : <span className="muted-text">-</span>}
                      </td>
                      <td data-label="Claude should">
                        <span className={`badge ${automatic ? "good" : "strong"}`}>{automatic ? "Apply automatically" : "Ask first"}</span>
                      </td>
                      <td data-label="Record">
                        <div className="row-actions">
                          <button
                            type="button"
                            className="compact-button"
                            disabled={busyJob === job.job_id}
                            onClick={() => void record(job, true)}
                          >
                            Mark as applied
                          </button>
                          <button
                            type="button"
                            className="ghost-button compact-button"
                            disabled={busyJob === job.job_id}
                            onClick={() => void record(job, false)}
                          >
                            Skip
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Log a LinkedIn or Indeed application</h2>
        <LogApplied
          onSaved={async (title) => {
            await load();
            window.dispatchEvent(new Event("applications-changed"));
            setNotice({ type: "success", message: `Recorded as applied: ${title}.` });
          }}
        />
      </section>

      <section className="panel">
        <h2>Applied in the last {RECENT_DAYS} days</h2>
        {recent.length === 0 ? (
          <p className="muted-text">Nothing yet.</p>
        ) : (
          <ul className="profile-list">
            {recent.map((item) => (
              <li key={item.id}>
                {item.title}
                {item.company ? ` · ${item.company}` : ""} · {SOURCE_LABELS[item.source] ?? item.source} · {formatDate(item.applied_at)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <CandidateDetails candidate={candidate} />
      <CvPanel cvText={candidate.cv_text} />
    </div>
  );
}

function LogApplied({ onSaved }: { onSaved: (title: string) => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [source, setSource] = useState<ApplicationSource>("linkedin");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.createApplication({ title, company: company || undefined, url: url || undefined, source, status: "applied" });
      await onSaved(title);
      setTitle("");
      setCompany("");
      setUrl("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that application.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="profile-form log-form" onSubmit={submit}>
      <div className="form-grid">
        <label>
          Job title
          <input value={title} onChange={(event) => setTitle(event.target.value)} required />
        </label>
        <label>
          Company
          <input value={company} onChange={(event) => setCompany(event.target.value)} />
        </label>
        <label>
          Link to the job
          <input type="url" placeholder="https://" value={url} onChange={(event) => setUrl(event.target.value)} />
        </label>
        <label>
          Site
          <select value={source} onChange={(event) => setSource(event.target.value as ApplicationSource)}>
            <option value="linkedin">LinkedIn</option>
            <option value="indeed">Indeed</option>
            <option value="other">Other</option>
          </select>
        </label>
      </div>
      {error ? <div className="notice-banner error">{error}</div> : null}
      <div className="action-row">
        <button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Record as applied"}
        </button>
      </div>
    </form>
  );
}
