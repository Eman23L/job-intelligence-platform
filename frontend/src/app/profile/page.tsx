"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
  const [roles, setRoles] = useState("");
  const [place, setPlace] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [savingSearch, setSavingSearch] = useState(false);
  const router = useRouter();

  useEffect(() => {
    api
      .profile()
      .then((result) => {
        setProfile(result);
        setCvText(result?.cv_text ?? "");
        fillSearchFields(result);
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
      if (!roles.trim()) {
        fillSearchFields(saved);
      }
      // Match scores come from the CV, so re-score jobs whenever it changes.
      await api.rescoreJobs().catch(() => undefined);
      setNotice("CV saved. Next, check the jobs you want below.");
      document.getElementById("wanted")?.scrollIntoView({ behavior: "smooth" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save CV profile");
    } finally {
      setSaving(false);
    }
  };

  const saveSearch = async (event: FormEvent) => {
    event.preventDefault();
    setSavingSearch(true);
    setError(null);
    try {
      const wanted = roles
        .split(",")
        .map((role) => role.trim())
        .filter(Boolean)
        .slice(0, 5);
      const saved = await api.saveSearchPreferences({ roles: wanted, location: place.trim() || null, remote_only: remoteOnly });
      setProfile(saved);
      // Search straight away for the new roles, then show the results.
      await api.newSearch().catch(() => undefined);
      router.push("/find");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the jobs you want");
      setSavingSearch(false);
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
          <div>
            <h2>1. Your CV</h2>
            <p className="muted-text">Every job is matched against this. The file is what gets attached to your applications.</p>
          </div>
          <span className="muted-text">
            {profile?.updated_at ? `Updated ${formatDate(profile.updated_at)}` : "No CV saved yet"}
          </span>
        </div>
        <form className="profile-form" onSubmit={saveCv}>
          <label>
            CV file (used when applying)
            <input type="file" accept=".pdf,.doc,.docx" disabled={fileUploading} onChange={(event) => void uploadCvFile(event.target.files?.[0] ?? null)} />
            <span className="muted-text">{profile?.cv_file_name ? `Current file: ${profile.cv_file_name}` : "PDF or Word document"}</span>
          </label>
          <label>
            CV text (used for scoring)
            <textarea
              className="cv-textarea"
              value={cvText}
              onChange={(event) => setCvText(event.target.value)}
              placeholder="Paste the text of your CV here"
              required
            />
          </label>
          <div className="action-row">
            <button type="submit" disabled={saving || !cvText.trim()}>
              {saving ? "Saving..." : "Save CV"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel" id="wanted">
        <div className="panel-header">
          <div>
            <h2>2. What jobs do you want?</h2>
            <p className="muted-text">
              Up to five roles. They don&apos;t have to be on your CV yet: if you want to move into something new, add it here.
            </p>
          </div>
        </div>
        <form className="profile-form" onSubmit={saveSearch}>
          <div className="form-grid">
            <label>
              Roles (comma separated)
              <input value={roles} placeholder="e.g. Junior Python Developer, Data Analyst" onChange={(event) => setRoles(event.target.value)} />
            </label>
            <label>
              Where
              <input value={place} placeholder="e.g. Milton Keynes" disabled={remoteOnly} onChange={(event) => setPlace(event.target.value)} />
            </label>
            <label className="checkbox-label form-grid-checkbox">
              <input type="checkbox" checked={remoteOnly} onChange={(event) => setRemoteOnly(event.target.checked)} />
              Remote jobs only
            </label>
          </div>
          <div className="action-row">
            <button type="submit" disabled={savingSearch || !profile || !roles.trim()}>
              {savingSearch ? <span className="spinner" aria-hidden="true" /> : null}
              Save and find jobs
            </button>
            {!profile ? <span className="muted-text">Save your CV first.</span> : null}
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>3. Your details for applications</h2>
            <p className="muted-text">Claude uses these to fill in application forms for you.</p>
          </div>
        </div>
        <form className="profile-form" onSubmit={saveApplicationProfile}>
          <fieldset className="form-section">
            <legend>Contact</legend>
            <div className="form-grid">
              <ProfileInput label="First name" value={profile?.first_name ?? ""} onChange={(value) => setProfileField("first_name", value)} />
              <ProfileInput label="Last name" value={profile?.last_name ?? ""} onChange={(value) => setProfileField("last_name", value)} />
              <ProfileInput label="Email" value={profile?.email ?? ""} onChange={(value) => setProfileField("email", value)} />
              <ProfileInput label="Phone" value={profile?.phone ?? ""} onChange={(value) => setProfileField("phone", value)} />
              <ProfileInput label="Address" value={profile?.address ?? ""} onChange={(value) => setProfileField("address", value)} />
              <ProfileInput label="Country" value={profile?.country ?? ""} onChange={(value) => setProfileField("country", value)} />
            </div>
          </fieldset>
          <fieldset className="form-section">
            <legend>Job preferences</legend>
            <div className="form-grid">
              <ProfileInput label="UK work status" value={profile?.work_status_uk ?? ""} onChange={(value) => setProfileField("work_status_uk", value)} />
              <ProfileSelect
                label="Notice period"
                value={profile?.availability_notice ?? ""}
                options={["Immediate", "1 Week", "2 Weeks", "3 Weeks", "1 Month", "3 Months", ">3 Months"]}
                onChange={(value) => setProfileField("availability_notice", value)}
              />
              <ProfileSelect
                label="Salary expectation"
                value={String(profile?.salary_expectation_gbp ?? "")}
                options={Object.keys(SALARY_BANDS)}
                optionLabels={SALARY_BANDS}
                onChange={(value) => {
                  setProfileField("salary_expectation_gbp", value ? Number(value) : null);
                  // Free-text forms read salary_expectation, so keep it in step with the band.
                  setProfileField("salary_expectation", value ? SALARY_BANDS[value] : "");
                }}
              />
              <ProfileSelect
                label="Willing to travel"
                value={String(profile?.travel_distance_miles ?? "")}
                options={Object.keys(TRAVEL_BANDS)}
                optionLabels={TRAVEL_BANDS}
                onChange={(value) => {
                  setProfileField("travel_distance_miles", value ? Number(value) : null);
                  setProfileField("travel_distance", value ? TRAVEL_BANDS[value] : "");
                }}
              />
              <label className="checkbox-label form-grid-checkbox">
                <input
                  type="checkbox"
                  checked={profile?.sponsorship_required ?? false}
                  onChange={(event) => setProfileField("sponsorship_required", event.target.checked)}
                />
                I need visa sponsorship
              </label>
            </div>
          </fieldset>
          <div className="action-row">
            <button type="submit" disabled={saving || !profile}>
              {saving ? "Saving..." : "Save details"}
            </button>
            {!profile ? <span className="muted-text">Save your CV first.</span> : null}
          </div>
        </form>
      </section>

      {profile ? (
        <details className="panel collapsible-panel">
          <summary>
            <h2>What we read from your CV</h2>
          </summary>
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
        </details>
      ) : null}
      <p className="muted-text">
        Done? <Link href="/find">See the jobs found for you →</Link>
      </p>
    </div>
  );

  function fillSearchFields(source: UserProfile | null) {
    if (!source) {
      return;
    }
    setRoles((source.search_roles?.length ? source.search_roles : source.preferred_roles).slice(0, 5).join(", "));
    setPlace(source.search_location ?? source.location_preference?.split("/")[0]?.trim() ?? "");
    setRemoteOnly(source.search_remote_only);
  }

  function setProfileField(key: keyof UserProfile, value: string | boolean | number | null) {
    setProfile((current) => (current ? { ...current, [key]: value } : current));
  }
}

const SALARY_BANDS: Record<string, string> = {
  "15000": "Up to £15,000",
  "20000": "£15,000 – £20,000",
  "25000": "£20,000 – £25,000",
  "30000": "£25,000 – £30,000",
  "40000": "£30,000 – £40,000",
  "50000": "£40,000 – £50,000",
  "65000": "£50,000 – £75,000",
  "90000": "£75,000 – £100,000",
  "100001": "Over £100,000"
};

const TRAVEL_BANDS: Record<string, string> = {
  "5": "Up to 5 miles",
  "15": "6 – 15 miles",
  "30": "16 – 30 miles",
  "50": "31 – 50 miles",
  "51": "More than 50 miles"
};

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
