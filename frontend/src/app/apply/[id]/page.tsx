"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CandidateDetails, CopyBlock, CvPanel } from "@/components/CandidateDetails";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import { STATUS_LABELS } from "@/lib/applications";
import { formatDate } from "@/lib/format";
import type { ApplyPack } from "@/types/api";

export default function ApplyPage({ params }: { params: { id: string } }) {
  const [pack, setPack] = useState<ApplyPack | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [pageUrl, setPageUrl] = useState("");

  useEffect(() => {
    setPageUrl(window.location.href);
    api
      .applyPack(params.id)
      .then(setPack)
      .catch((err: Error) => setError(err.message));
  }, [params.id]);

  const instructions = useMemo(() => (pack ? claudeInstructions(pack, pageUrl) : ""), [pack, pageUrl]);

  const setStatus = async (status: "applied" | "skipped" | "to_apply") => {
    if (!pack) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const application = await api.updateApplication(pack.application.id, { status });
      setPack({ ...pack, application });
      setMessage({
        type: "success",
        text: status === "applied" ? "Marked as applied. Good luck!" : status === "skipped" ? "Skipped." : "Queued for Claude again."
      });
    } catch (err) {
      setMessage({ type: "error", text: err instanceof Error ? err.message : "Couldn't update this application." });
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return <ErrorState message={error} />;
  }
  if (!pack) {
    return <LoadingState label="Loading application" />;
  }

  const { job, candidate, application } = pack;

  return (
    <div className="page-stack apply-pack">
      <Link href="/applications" className="back-link">
        ← Applications
      </Link>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>{job.title}</h2>
            <p className="muted">{[job.company, job.location, job.salary].filter(Boolean).join(" · ")}</p>
            <span className={`badge ${application.status === "to_apply" ? "strong" : application.status === "skipped" ? "neutral" : "good"}`}>
              {STATUS_LABELS[application.status]}
            </span>
          </div>
          <div className="action-row">
            {job.url ? (
              <a href={job.url} target="_blank" rel="noreferrer" className="button-link">
                Open the job listing ↗
              </a>
            ) : null}
            {application.status === "to_apply" ? (
              <>
                <button type="button" className="secondary-button" disabled={busy} onClick={() => void setStatus("applied")}>
                  I&apos;ve applied
                </button>
                <button type="button" className="ghost-button" disabled={busy} onClick={() => void setStatus("skipped")}>
                  Skip
                </button>
              </>
            ) : (
              <button type="button" className="secondary-button" disabled={busy} onClick={() => void setStatus("to_apply")}>
                Queue for Claude again
              </button>
            )}
          </div>
        </div>
        {message ? <div className={`notice-banner ${message.type}`}>{message.text}</div> : null}
      </section>

      <section className="panel claude-steps">
        <h2>Apply with Claude in Chrome</h2>
        <ol className="steps">
          <li>Open the job listing in a new tab and keep this page open too.</li>
          <li>Copy the instructions below and paste them into Claude in Chrome.</li>
          <li>Claude fills in the form from the details on this page and stops before submitting.</li>
          <li>Check everything, attach your CV if the site asks for it, then press Submit yourself.</li>
          <li>Come back here and choose &quot;I&apos;ve applied&quot;.</li>
        </ol>
        <CopyBlock label="Instructions for Claude" text={instructions} rows={15} />
      </section>

      <CandidateDetails candidate={candidate} />

      <div className="detail-grid">
        <section className="panel">
          <h2>About the job</h2>
          {job.posted_at ? <p className="muted-text">Posted {formatDate(job.posted_at)}</p> : null}
          <p className="description">{job.description ?? "No description saved for this job. Read it on the listing."}</p>
        </section>
        <CvPanel cvText={candidate.cv_text} />
      </div>
    </div>
  );
}

function claudeInstructions(pack: ApplyPack, pageUrl: string): string {
  const { job } = pack;
  const role = [job.title, job.company].filter(Boolean).join(" at ");
  return [
    `Please help me apply for this job: ${role}.`,
    job.url ? `Job listing: ${job.url}` : "The job listing is linked at the top of my apply page.",
    "",
    `My details are on my private apply page: ${pageUrl}`,
    "Read that page, but don't change anything on it.",
    "",
    "Fill in the application using only those details:",
    "- Use my name, email, phone, address, right to work, notice period, salary expectation and travel distance exactly as given.",
    "- For a cover letter or \"why do you want this job\" box, write a short, specific answer (under 150 words) based on my CV and the job description. Use plain, friendly British English with no emojis and no exaggeration.",
    "- If the site needs my CV file, tell me and I'll attach it.",
    "- If it asks me to create an account, shows a CAPTCHA, or asks something my details don't cover, stop and ask me.",
    "- Don't press the final Submit or Apply button. Stop and tell me when it's ready for me to check."
  ].join("\n");
}
