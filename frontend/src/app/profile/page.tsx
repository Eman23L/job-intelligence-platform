"use client";

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { SkillBadge } from "@/components/SkillBadge";
import { api } from "@/lib/api";
import { formatDate, formatSalary } from "@/lib/format";
import type { UserProfile } from "@/types/api";

export default function ProfilePage() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [cvText, setCvText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .profile()
      .then((result) => {
        setProfile(result);
        setCvText(result?.cv_text ?? "");
        setError(null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const saveCv = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveProfileCv(cvText);
      setProfile(saved);
      setCvText(saved.cv_text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save CV profile");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <LoadingState label="Loading profile" />;
  }

  return (
    <div className="page-stack">
      {error ? <ErrorState message={error} /> : null}

      <section className="panel">
        <div className="panel-header">
          <h2>CV text</h2>
          <span className="muted-text">
            {profile?.updated_at ? `Updated ${formatDate(profile.updated_at)}` : "No CV saved"}
          </span>
        </div>
        <form className="profile-form" onSubmit={saveCv}>
          <label>
            Paste CV
            <textarea
              className="cv-textarea"
              value={cvText}
              onChange={(event) => setCvText(event.target.value)}
              placeholder="Paste CV text here"
              required
            />
          </label>
          <div className="action-row">
            <button type="submit" disabled={saving || !cvText.trim()}>
              {saving ? "Saving" : "Save CV"}
            </button>
          </div>
        </form>
      </section>

      {profile ? (
        <section className="panel">
          <div className="panel-header">
            <h2>Extracted profile</h2>
          </div>
          <div className="profile-summary-grid">
            <SummaryBlock title="Skills">
              <div className="badge-list">
                {profile.skills.length ? (
                  profile.skills.map((skill) => <SkillBadge key={skill} label={skill} tone="good" />)
                ) : (
                  <span className="muted-text">None extracted</span>
                )}
              </div>
            </SummaryBlock>
            <SummaryBlock title="Preferred roles">
              <ListItems items={profile.preferred_roles} />
            </SummaryBlock>
            <SummaryBlock title="Preferences">
              <div className="metric-list">
                <Metric label="Location" value={profile.location_preference ?? "Not listed"} />
                <Metric label="Remote" value={profile.remote_preference ?? "Not listed"} />
                <Metric
                  label="Salary"
                  value={formatSalary(profile.salary_min_preference, profile.salary_max_preference)}
                />
                <Metric label="Authorization" value={profile.preferences.work_authorization || "Not listed"} />
              </div>
            </SummaryBlock>
            <SummaryBlock title="Experience">
              <ListItems items={profile.experience} />
            </SummaryBlock>
            <SummaryBlock title="Projects">
              <ProjectItems items={profile.projects} />
            </SummaryBlock>
            <SummaryBlock title="Education">
              <ListItems items={profile.education} />
            </SummaryBlock>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function SummaryBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="profile-block">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function ListItems({ items }: { items: string[] }) {
  if (!items.length) {
    return <span className="muted-text">None extracted</span>;
  }
  return (
    <ul className="profile-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function ProjectItems({ items }: { items: string[] }) {
  if (!items.length) {
    return <span className="muted-text">None extracted</span>;
  }
  return (
    <div className="project-list">
      {items.map((item) => {
        const [title, ...description] = item.split(/\s[-:]\s/);
        return (
          <article key={item} className="project-item">
            <strong>{title}</strong>
            {description.length ? <span>{description.join(" - ")}</span> : null}
          </article>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
