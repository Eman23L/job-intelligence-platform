"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CandidateDetails, CopyBlock, CvPanel, type Candidate } from "@/components/CandidateDetails";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { api } from "@/lib/api";
import type { UserProfile } from "@/types/api";

type Site = "linkedin" | "indeed";

interface Search {
  titles: string;
  location: string;
  remoteOnly: boolean;
  postedDays: 1 | 7 | 14;
  limit: number;
}

export default function LinkedInIndeedPage() {
  const [profile, setProfile] = useState<UserProfile | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [site, setSite] = useState<Site>("linkedin");
  const [search, setSearch] = useState<Search>({ titles: "", location: "", remoteOnly: false, postedDays: 7, limit: 5 });
  const [origin, setOrigin] = useState("");

  useEffect(() => {
    setOrigin(window.location.origin);
    api
      .profile()
      .then((result) => {
        setProfile(result);
        if (result) {
          setSearch((current) => ({
            ...current,
            titles: result.preferred_roles.slice(0, 3).join(", "),
            location: result.location_preference?.split("/")[0]?.trim() ?? "",
            remoteOnly: result.remote_preference === "remote"
          }));
        }
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const searchUrl = useMemo(() => buildSearchUrl(site, search), [site, search]);
  const instructions = useMemo(() => buildInstructions(site, search, searchUrl, origin), [site, search, searchUrl, origin]);

  if (error) {
    return <ErrorState message={error} />;
  }
  if (profile === undefined) {
    return <LoadingState label="Loading your details" />;
  }
  if (profile === null) {
    return (
      <section className="panel">
        <h2>Add your CV first</h2>
        <p className="muted">Claude needs your CV and contact details to fill in applications.</p>
        <Link href="/profile" className="button-link">
          Go to Profile
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

  return (
    <div className="page-stack">
      <section className="panel">
        <h2>How this works</h2>
        <p className="muted">
          LinkedIn and Indeed don&apos;t allow automated tools, so Claude works in your own browser, signed in as you, one job at a time while
          you watch. You check each application and press Submit yourself, and Claude logs it in your{" "}
          <Link href="/applications">Applications</Link> tracker.
        </p>
      </section>

      <div className="tab-row" role="tablist" aria-label="Job site">
        {(
          [
            ["linkedin", "LinkedIn"],
            ["indeed", "Indeed"]
          ] as const
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={site === key} className={site === key ? "tab active" : "tab"} onClick={() => setSite(key)}>
            {label}
          </button>
        ))}
      </div>

      <section className="panel">
        <h2>1. What to search for</h2>
        <div className="form-grid">
          <label>
            Job titles (comma separated)
            <input
              value={search.titles}
              placeholder="e.g. Data Engineer, Automation Engineer"
              onChange={(event) => setSearch({ ...search, titles: event.target.value })}
            />
          </label>
          <label>
            Location
            <input value={search.location} placeholder="e.g. Milton Keynes" onChange={(event) => setSearch({ ...search, location: event.target.value })} />
          </label>
          <label>
            Posted within
            <select value={search.postedDays} onChange={(event) => setSearch({ ...search, postedDays: Number(event.target.value) as Search["postedDays"] })}>
              <option value={1}>Last 24 hours</option>
              <option value={7}>Last week</option>
              <option value={14}>Last 2 weeks</option>
            </select>
          </label>
          <label>
            Applications this session
            <select value={search.limit} onChange={(event) => setSearch({ ...search, limit: Number(event.target.value) })}>
              {[3, 5, 10].map((value) => (
                <option key={value} value={value}>
                  Up to {value}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox-label form-grid-checkbox">
            <input type="checkbox" checked={search.remoteOnly} onChange={(event) => setSearch({ ...search, remoteOnly: event.target.checked })} />
            Remote jobs only
          </label>
        </div>
        <p className="muted-text search-preview">
          Search link:{" "}
          <a href={searchUrl} target="_blank" rel="noreferrer">
            open {site === "linkedin" ? "LinkedIn" : "Indeed"} with this search ↗
          </a>
        </p>
      </section>

      <section className="panel">
        <h2>2. Give Claude these instructions</h2>
        <p className="muted-text">Open Claude in Chrome, paste this, and stay nearby to check each application.</p>
        <CopyBlock label={`Instructions for Claude (${site === "linkedin" ? "LinkedIn" : "Indeed"})`} text={instructions} rows={18} />
      </section>

      <CandidateDetails candidate={candidate} />
      <CvPanel cvText={candidate.cv_text} />
    </div>
  );
}

function titleList(search: Search): string[] {
  return search.titles
    .split(",")
    .map((title) => title.trim())
    .filter(Boolean);
}

function buildSearchUrl(site: Site, search: Search): string {
  const titles = titleList(search);
  const keywords = titles.length > 1 ? titles.map((title) => `"${title}"`).join(" OR ") : titles[0] ?? "";
  if (site === "linkedin") {
    const params = new URLSearchParams({ keywords, location: search.location || "United Kingdom", f_AL: "true", f_TPR: `r${search.postedDays * 86400}` });
    if (search.remoteOnly) {
      params.set("f_WT", "2");
    }
    return `https://www.linkedin.com/jobs/search/?${params.toString()}`;
  }
  // Indeed: search the first title; the instructions ask Claude to run the others afterwards.
  const first = titles[0] ?? "";
  const params = new URLSearchParams({ q: search.remoteOnly ? `${first} remote`.trim() : first, l: search.location, fromage: String(search.postedDays) });
  return `https://uk.indeed.com/jobs?${params.toString()}`;
}

function buildInstructions(site: Site, search: Search, searchUrl: string, origin: string): string {
  const name = site === "linkedin" ? "LinkedIn" : "Indeed";
  const quickApply = site === "linkedin" ? "Easy Apply" : "\"Easily apply\"";
  return [
    `Please help me apply for jobs on ${name}, one at a time, while I watch.`,
    "",
    `1. Open this search: ${searchUrl}`,
    `2. My details and CV are on my private details page: ${origin}/linkedin-indeed`,
    "   Read that page, but don't change anything on it.",
    `3. Go through the results in order, looking at jobs with ${quickApply}. For each one:`,
    "   - Read the description. Skip it if it needs experience my CV doesn't show, is a different kind of role, or is a company I've already applied to this session. Tell me in one line why you skipped it.",
    `   - If it fits, start ${quickApply} and fill in the form using only my details. Answer screening questions honestly from my CV. If a question isn't covered by my details, ask me.`,
    "   - If there's a cover letter box, write a short, specific note (under 150 words) in plain, friendly British English with no emojis.",
    "   - If it needs my CV file, tell me and I'll attach it.",
    "   - Don't press the final Submit button. Stop and wait for me to check and submit it.",
    `   - After I've submitted, open ${origin}/applications, choose "Add a job from LinkedIn, Indeed...", and save the job title, company and link, with Found on: ${name} and Status: I've applied.`,
    ...(site === "indeed" && titleList(search).length > 1
      ? [`   - When you reach the end of the results, search ${name} again for: ${titleList(search).slice(1).join(", ")}.`]
      : []),
    `4. Stop after ${search.limit} applications, or sooner if I say so.`,
    "",
    `Don't message anyone, follow companies, or change my ${name} profile or settings.`
  ].join("\n");
}
