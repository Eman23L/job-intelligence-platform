"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ApplyPack } from "@/types/api";

export type Candidate = ApplyPack["candidate"];

// The person's application details, laid out so both they and Claude in Chrome can read them.
export function CandidateDetails({ candidate }: { candidate: Candidate }) {
  const fullName = [candidate.first_name, candidate.last_name].filter(Boolean).join(" ");
  const details: Array<[string, string | null]> = [
    ["Full name", fullName || null],
    ["First name", candidate.first_name],
    ["Last name", candidate.last_name],
    ["Email", candidate.email],
    ["Phone", candidate.phone],
    ["Address", candidate.address],
    ["Country", candidate.country],
    ["Right to work in the UK", candidate.work_status_uk],
    ["Needs visa sponsorship", candidate.sponsorship_required ? "Yes" : "No"],
    ["Notice period", candidate.availability_notice],
    ["Salary expectation", candidate.salary_expectation],
    ["Willing to travel", candidate.travel_distance]
  ];
  const missing = details.filter(([, value]) => !value).map(([label]) => label);

  return (
    <section className="panel" aria-labelledby="your-details">
      <div className="panel-header">
        <h2 id="your-details">Your details for applications</h2>
        {candidate.cv_file_name ? <DownloadCvButton fileName={candidate.cv_file_name} /> : null}
      </div>
      {missing.length ? (
        <div className="notice-banner warning">
          Missing: {missing.join(", ")}. <Link href="/profile">Add them on your Profile</Link> so Claude can fill them in.
        </div>
      ) : null}
      <dl className="detail-list">
        {details.map(([label, value]) => (
          <div key={label} className="detail-row">
            <dt>{label}</dt>
            <dd>{value ?? <span className="muted-text">Not set</span>}</dd>
            {value ? <CopyButton text={value} label={label} /> : <span />}
          </div>
        ))}
        <div className="detail-row">
          <dt>CV file</dt>
          <dd>{candidate.cv_file_name ?? <span className="muted-text">No file uploaded. Add one on your Profile.</span>}</dd>
          <span />
        </div>
      </dl>
    </section>
  );
}

export function CvPanel({ cvText }: { cvText: string | null }) {
  return (
    <section className="panel">
      <h2>Your CV</h2>
      <p className="muted-text">For writing cover notes and answering questions about your experience.</p>
      <p className="description cv-text">{cvText ?? "No CV saved yet. Add it on your Profile."}</p>
    </section>
  );
}

export function CopyBlock({ label, text, rows }: { label: string; text: string; rows: number }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-block">
      <label>
        {label}
        <textarea readOnly rows={rows} value={text} onFocus={(event) => event.currentTarget.select()} />
      </label>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard
            .writeText(text)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
      >
        {copied ? "Copied" : "Copy instructions"}
      </button>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="ghost-button compact-button"
      aria-label={`Copy ${label}`}
      onClick={() => {
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => setCopied(false));
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function DownloadCvButton({ fileName }: { fileName: string }) {
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  return (
    <button
      type="button"
      className="secondary-button compact-button"
      disabled={state === "busy"}
      onClick={() => {
        setState("busy");
        api
          .downloadCv()
          .then(() => setState("idle"))
          .catch(() => setState("error"));
      }}
    >
      {state === "error" ? "Download failed, try again" : `Download CV (${fileName})`}
    </button>
  );
}
