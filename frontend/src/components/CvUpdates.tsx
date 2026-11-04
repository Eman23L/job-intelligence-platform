"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { CvChangedBy, CvUpdates as CvUpdatesData, UserProfile } from "@/types/api";

const WHO: Record<CvChangedBy, string> = {
  claude: "Claude updated it",
  you: "You changed it",
  undo: "Undone"
};

// On My CV: what Claude last changed (with undo), news for the next refresh, and earlier versions.
export function CvUpdates({ onProfileChange }: { onProfileChange: (profile: UserProfile, message: string) => void }) {
  const [updates, setUpdates] = useState<CvUpdatesData | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    const next = await api.cvUpdates();
    setUpdates(next);
    setNotes(next.notes);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (err) {
      setMessage({ type: "error", text: err instanceof Error ? err.message : "Something went wrong." });
    } finally {
      setBusy(false);
    }
  };

  const saveNotes = () =>
    run(async () => {
      const next = await api.saveCvNotes(notes);
      setUpdates(next);
      setNotes(next.notes);
      setMessage({ type: "success", text: next.notes ? "Saved. Claude adds it at the next refresh." : "Notes cleared." });
    });

  const restore = (id: number, label: string) =>
    run(async () => {
      const profile = await api.restoreCvVersion(id);
      await load();
      onProfileChange(profile, label);
    });

  const makePdf = () =>
    run(async () => {
      const profile = await api.makeCvPdf();
      await load();
      onProfileChange(profile, `Made ${profile.cv_file_name ?? "a PDF"} from your CV text. It's attached to applications from now on.`);
    });

  if (!updates) {
    return null;
  }
  const latest = updates.versions[0];
  const claudeChanged = latest?.changed_by === "claude";

  return (
    <div className="cv-updates">
      <h3>Kept up to date by Claude</h3>
      <p className="muted-text">
        Every {updates.refresh_every_days} days, during the daily run, Claude checks for anything new and adds it to your CV. The PDF is
        rebuilt to match. {updates.checked_at ? `Last checked ${formatDate(updates.checked_at)}.` : "Not checked yet."}
      </p>

      {claudeChanged ? (
        <div className="notice-banner info">
          <strong>Claude updated your CV on {formatDate(latest.created_at)}:</strong>
          <ul className="profile-list">
            {latest.change_note.split("\n").map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="muted-text">JobServe, LinkedIn and Indeed send the CV saved on each site, so upload the new PDF there too.</p>
          <div className="action-row">
            <button type="button" className="compact-button" onClick={() => void api.downloadCv()}>
              Download the new PDF
            </button>
            <button
              type="button"
              className="ghost-button compact-button"
              disabled={busy}
              onClick={() => void restore(latest.id, "Undone. Your CV is back to how it was before Claude's update.")}
            >
              Undo this update
            </button>
          </div>
        </div>
      ) : null}

      <label>
        Anything new for your CV?
        <textarea
          rows={3}
          value={notes}
          maxLength={4000}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="A course you finished, a certificate, a new project, a promotion. One line is enough."
        />
      </label>
      <div className="action-row">
        <button type="button" className="compact-button" disabled={busy || notes === updates.notes} onClick={() => void saveNotes()}>
          Save for Claude
        </button>
        <button type="button" className="ghost-button compact-button" disabled={busy} onClick={() => void makePdf()}>
          Make the PDF from my saved CV text
        </button>
      </div>
      {message ? <div className={`notice-banner ${message.type}`}>{message.text}</div> : null}

      {updates.versions.length ? (
        <details className="cv-history">
          <summary>Earlier versions ({updates.versions.length})</summary>
          <ul className="profile-list">
            {updates.versions.map((version) => (
              <li key={version.id}>
                <span>
                  {formatDate(version.created_at)} · {WHO[version.changed_by] ?? version.changed_by}:{" "}
                  {version.change_note.split("\n").join("; ")}
                </span>{" "}
                <button
                  type="button"
                  className="ghost-button compact-button"
                  disabled={busy}
                  onClick={() => void restore(version.id, `Your CV is back to the version from before ${formatDate(version.created_at)}.`)}
                >
                  Go back to the CV before this
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
