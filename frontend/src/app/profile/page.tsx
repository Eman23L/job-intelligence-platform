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
  const [fileUploading, setFileUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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
      setNotice("CV text saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save CV profile");
    } finally {
      setSaving(false);
    }
  };

  const saveApplicationProfile = async (event: FormEvent) => {
    event.preventDefault();
    if (!profile) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveApplicationProfile(profile);
      setProfile(saved);
      setNotice("Application profile saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save application profile");
    } finally {
      setSaving(false);
    }
  };

  const uploadCvFile = async (file: File | null) => {
    if (!file) {
      return;
    }
    setFileUploading(true);
    setError(null);
    try {
      const saved = await api.uploadProfileCvFile(file);
      setProfile(saved);
      setNotice("CV file uploaded.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to upload CV file");
    } finally {
      setFileUploading(false);
    }
  };

  if (loading) {
    return <LoadingState label="Loading profile" />;
  }

  return (
    <div className="page-stack">
      {error ? <ErrorState message={error} /> : null}
      {notice ? <div className="notice-banner success">{notice}</div> : null}

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

      <section className="panel">
        <div className="panel-header">
          <h2>Application details</h2>
          <span className="muted-text">{profile?.cv_file_name ? `CV file: ${profile.cv_file_name}` : "No CV file uploaded"}</span>
        </div>
        <form className="profile-form" onSubmit={saveApplicationProfile}>
          <div className="profile-summary-grid">
            <ProfileInput label="Email" value={profile?.email ?? ""} onChange={(value) => setProfileField("email", value)} />
            <ProfileInput label="First name" value={profile?.first_name ?? ""} onChange={(value) => setProfileField("first_name", value)} />
            <ProfileInput label="Last name" value={profile?.last_name ?? ""} onChange={(value) => setProfileField("last_name", value)} />
            <ProfileInput label="Phone" value={profile?.phone ?? ""} onChange={(value) => setProfileField("phone", value)} />
            <ProfileInput label="Address" value={profile?.address ?? ""} onChange={(value) => setProfileField("address", value)} />
            <ProfileInput label="Country" value={profile?.country ?? ""} onChange={(value) => setProfileField("country", value)} />
            <ProfileInput label="UK work status" value={profile?.work_status_uk ?? ""} onChange={(value) => setProfileField("work_status_uk", value)} />
            <ProfileInput label="Salary expectation" value={profile?.salary_expectation ?? ""} onChange={(value) => setProfileField("salary_expectation", value)} />
            <ProfileInput label="Travel distance" value={profile?.travel_distance ?? ""} onChange={(value) => setProfileField("travel_distance", value)} />
            <ProfileSelect
              label="Availability notice"
              value={profile?.availability_notice ?? ""}
              options={["Immediate", "1 Week", "2 Weeks", "3 Weeks", "1 Month", "3 Months", ">3 Months"]}
              onChange={(value) => setProfileField("availability_notice", value)}
            />
            <ProfileSelect
              label="Salary expectation GBP"
              value={String(profile?.salary_expectation_gbp ?? "")}
              options={["15000", "20000", "25000", "30000", "40000", "50000", "65000", "90000", "100001"]}
              optionLabels={{
                "15000": "0 - £15,000",
                "20000": "£15,000 - £20,000",
                "25000": "£20,000 - £25,000",
                "30000": "£25,000 - £30,000",
                "40000": "£30,000 - £40,000",
                "50000": "£40,000 - £50,000",
                "65000": "£50,000 - £75,000",
                "90000": "£75,000 - £100,000",
                "100001": "Above £100,000"
              }}
              onChange={(value) => setProfileField("salary_expectation_gbp", value ? Number(value) : null)}
            />
            <ProfileSelect
              label="Travel distance miles"
              value={String(profile?.travel_distance_miles ?? "")}
              options={["5", "15", "30", "50", "51"]}
              optionLabels={{ "5": "0 to 5", "15": "6 to 15", "30": "16 to 30", "50": "31 to 50", "51": "50+" }}
              onChange={(value) => setProfileField("travel_distance_miles", value ? Number(value) : null)}
            />
            <ProfileSelect
              label="Minimum apply score"
              value={String(profile?.minimum_apply_score ?? 80)}
              options={["60", "70", "80", "85", "90"]}
              optionLabels={{ "60": "60+", "70": "70+", "80": "80+", "85": "85+", "90": "90+" }}
              onChange={(value) => setProfileField("minimum_apply_score", Number(value))}
            />
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={profile?.sponsorship_required ?? false}
                onChange={(event) => setProfileField("sponsorship_required", event.target.checked)}
              />
              Sponsorship required
            </label>
          </div>
          <label>
            CV file
            <input type="file" accept=".pdf,.doc,.docx" disabled={fileUploading} onChange={(event) => void uploadCvFile(event.target.files?.[0] ?? null)} />
          </label>
          <div className="action-row">
            <button type="submit" disabled={saving || !profile}>
              {saving ? "Saving" : "Save application details"}
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
                <Metric label="Target seniority" value={formatSeniority(profile.preferences.target_seniority)} />
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

  function setProfileField(key: keyof UserProfile, value: string | boolean | number | null) {
    setProfile((current) => (current ? { ...current, [key]: value } : current));
  }
}

function ProfileInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label>
      {label}
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function ProfileSelect({
  label,
  value,
  options,
  optionLabels,
  onChange
}: {
  label: string;
  value: string;
  options: string[];
  optionLabels?: Record<string, string>;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Not set</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabels?.[option] ?? option}
          </option>
        ))}
      </select>
    </label>
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

function formatSeniority(value: string | undefined): string {
  if (!value) {
    return "Mid-senior";
  }
  return value.replaceAll("_", "-").replace(/^\w/, (letter) => letter.toUpperCase());
}
