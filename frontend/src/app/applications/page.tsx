"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LoadingState } from "@/components/LoadingState";
import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { RecommendationActionBadge } from "@/components/RecommendationActionBadge";
import { RecommendationBadge } from "@/components/RecommendationBadge";
import { ScoreBadge } from "@/components/ScoreBadge";
import { ApiError, api, apiConfig } from "@/lib/api";
import type { ApplicationItem, ApplicationPrepareRunStatus, ApplicationsList, AssistApplyDiagnosticRun, AssistApplyResult, AutonomousRealSubmitRunResult, AutonomousRealSubmitStatus, JobScorecard } from "@/types/api";

const RECENT_CHECK_MS = 24 * 60 * 60 * 1000;

export default function ApplicationsPage() {
  const [data, setData] = useState<ApplicationsList | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<number | "prepare" | null>(null);
  const [prepareRunId, setPrepareRunId] = useState<number | null>(null);
  const [prepareRun, setPrepareRun] = useState<ApplicationPrepareRunStatus | null>(null);
  const [preparePollFailures, setPreparePollFailures] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "info" | "success" | "warning" | "error"; message: string } | null>(null);
  const [scorecard, setScorecard] = useState<JobScorecard | null>(null);
  const [assistResult, setAssistResult] = useState<{ jobTitle: string; result: AssistApplyResult; diagnostic?: AssistApplyDiagnosticRun | null } | null>(null);
  const [assistDebugMode, setAssistDebugMode] = useState(true);
  const [autonomousStatus, setAutonomousStatus] = useState<AutonomousRealSubmitStatus | null>(null);
  const [autonomousResult, setAutonomousResult] = useState<AutonomousRealSubmitRunResult | null>(null);
  const [threshold, setThreshold] = useState(80);

  const refresh = useCallback(async () => {
    const result = await api.applications();
    setData(result);
    setThreshold(result.minimum_apply_score);
    api.autonomousRealSubmitStatus().then(setAutonomousStatus).catch(() => setAutonomousStatus(null));
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh()
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (!prepareRunId) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.prepareApplicationsRun(prepareRunId);
        if (cancelled) {
          return;
        }
        setPrepareRun(result);
        setPreparePollFailures(0);
        if (result.status === "completed" || result.status === "failed") {
          setPrepareRunId(null);
          setActionLoading(null);
          await refresh();
          setNotice({
            type: result.status === "completed" ? "success" : "error",
            message:
              result.status === "completed"
                ? `Prepare complete: ${result.queued} queued, ${result.skipped} skipped, ${result.failed} failed.`
                : result.error ?? "Prepare applications failed"
          });
        }
      } catch (err) {
        if (!cancelled) {
          setPreparePollFailures((current) => {
            const next = current + 1;
            if (next >= 3) {
              setNotice({ type: "warning", message: err instanceof Error ? err.message : "Temporary prepare status polling failure" });
            }
            return next;
          });
        }
      }
    };
    poll();
    const intervalId = globalThis.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      globalThis.clearInterval(intervalId);
    };
  }, [prepareRunId, refresh]);

  const prepareApplications = async () => {
    setActionLoading("prepare");
    setError(null);
    setNotice({ type: "info", message: "Preparing applications..." });
    setPrepareRun(null);
    setPreparePollFailures(0);
    try {
      const started = await api.prepareApplications();
      setPrepareRunId(started.run_id);
      setPrepareRun(emptyPrepareRun(started.run_id, started.status));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to prepare applications");
      setActionLoading(null);
    }
  };

  const saveThreshold = async (value: number) => {
    setThreshold(value);
    setError(null);
    try {
      await api.saveApplicationProfile({ minimum_apply_score: value });
      await refresh();
      setNotice({ type: "success", message: `Apply threshold set to ${value}+. Queue refreshed.` });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save apply threshold");
    }
  };

  const runJobAction = async (jobId: number, action: () => Promise<unknown>, successMessage: string) => {
    setActionLoading(jobId);
    setError(null);
    try {
      await action();
      await refresh();
      setNotice({ type: "success", message: successMessage });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Application action failed");
    } finally {
      setActionLoading(null);
    }
  };

  const openApplyLink = async (item: ApplicationItem) => {
    setActionLoading(item.job_id);
    setError(null);
    try {
      const checked = await api.checkJobAvailability(item.job_id);
      await refresh();
      if (checked.availability_status !== "active") {
        setNotice({
          type: "warning",
          message: `Application blocked because the job is ${checked.availability_status}. ${checked.availability_reason ?? ""}`.trim()
        });
        return;
      }
      window.open(item.apply_url, "_blank", "noopener,noreferrer");
      await api.markOpened(item.job_id);
      await refresh();
      setNotice({ type: "success", message: "Application link opened." });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to open apply link");
    } finally {
      setActionLoading(null);
    }
  };

  const viewScorecard = async (jobId: number) => {
    setActionLoading(jobId);
    setError(null);
    try {
      setScorecard(await api.jobScorecard(jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load scorecard");
    } finally {
      setActionLoading(null);
    }
  };

  const runAutonomousRealSubmit = async () => {
    setActionLoading("prepare");
    setError(null);
    try {
      const result = await api.runAutonomousRealSubmit();
      setAutonomousResult(result);
      await refresh();
      setNotice({
        type: result.submitted ? "success" : "warning",
        message: result.submitted ? "Autonomous real-submit canary submitted one application." : result.exact_error ?? result.recommended_fix
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to run autonomous real-submit canary");
    } finally {
      setActionLoading(null);
    }
  };

  const pollAssistResult = async (item: ApplicationItem, fallback: AssistApplyResult, expectDebug: boolean) => {
    let latestPersisted: AssistApplyResult | null = null;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await sleep(2000);
      const latest = await api.applications();
      setData(latest);
      setThreshold(latest.minimum_apply_score);
      const current = latest.items.find((candidate) => candidate.job_id === item.job_id);
      const persisted = current?.assisted_result;
      console.info("Assist apply applications poll payload", { attempt, jobId: item.job_id, assisted_result: persisted });
      if (!persisted) {
        continue;
      }
      latestPersisted = persisted;
      const displayedPersisted = workerProgressSeen(persisted) && persisted.status === "queued" ? { ...persisted, status: "running" } : persisted;
      setAssistResult({ jobTitle: current?.title ?? item.title, result: normaliseAssistResult(displayedPersisted) });
      if (persisted.status !== "queued" && (!expectDebug || persisted.debug_mode || persisted.final_error || persisted.debug_steps?.length)) {
        return persisted;
      }
    }
    if (latestPersisted && workerProgressSeen(latestPersisted)) {
      return latestPersisted.status === "queued" ? { ...latestPersisted, status: "running" } : latestPersisted;
    }
    return {
      ...normaliseAssistResult(fallback),
      status: "failed",
      final_error: "stale_queue_timeout",
      warnings: [...(fallback.warnings ?? []), "Worker did not pick up the assisted apply job within 2 minutes."],
      progress: {
        ...(fallback.progress ?? {}),
        current_step: "stale_queue_timeout",
        message: "Worker did not pick up the assisted apply job within 2 minutes."
      },
      jobserve_flow_diagnostics: {
        ...(fallback.jobserve_flow_diagnostics ?? {}),
        queue_failure: {
          error: "stale_queue_timeout",
          message: "Worker did not pick up the assisted apply job within 2 minutes."
        }
      }
    };
  };

  const pollAssistDiagnostic = async (item: ApplicationItem, started: AssistApplyDiagnosticRun) => {
    let latestRun = started;
    setAssistResult((current) => (current ? { ...current, diagnostic: latestRun } : current));
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (latestRun.status === "passed" || latestRun.status === "failed") {
        return latestRun;
      }
      await sleep(3000);
      latestRun = await api.assistApplyDiagnosticRun(item.job_id, latestRun.run_id);
      setAssistResult((current) => (current ? { ...current, diagnostic: latestRun } : current));
    }
    return latestRun;
  };

  const triggerSubmitFailureDiagnostic = async (item: ApplicationItem, failedResult: AssistApplyResult) => {
    try {
      const started = await api.startAssistApplyDiagnostic(item.job_id);
      setAssistResult({ jobTitle: item.title, result: normaliseAssistResult(failedResult), diagnostic: started });
      const completed = await pollAssistDiagnostic(item, started);
      setAssistResult({ jobTitle: item.title, result: normaliseAssistResult(failedResult), diagnostic: completed });
      const orchestration = await api.runAutonomousRealSubmit();
      setAutonomousResult(orchestration);
      return completed;
    } catch (err) {
      const diagnostic: AssistApplyDiagnosticRun = {
        run_id: "unavailable",
        status: "failed",
        latest_progress: { phase: "diagnostic_trigger_failed" },
        final_report: {
          failed_phase: "diagnostic_trigger_failed",
          exact_error: err instanceof Error ? err.message : "Unable to start safe diagnostic",
          recommended_fix: "Check backend diagnostic endpoint and worker queue health."
        },
        markdown_summary: null,
        artifact_links: []
      };
      setAssistResult({ jobTitle: item.title, result: normaliseAssistResult(failedResult), diagnostic });
      return diagnostic;
    }
  };

  const assistApply = async (item: ApplicationItem, mode: "review_only" | "submit_with_confirmation") => {
    if (mode === "submit_with_confirmation") {
      const confirmed = window.confirm("This will submit the application on JobServe using your saved CV. Continue?");
      if (!confirmed) {
        return;
      }
    }
    setActionLoading(item.job_id);
    setError(null);
    setAssistResult(null);
    try {
      const effectiveDebugMode = assistDebugMode || item.apply_strategy.startsWith("jobserve");
      console.info("Assist apply request", { jobId: item.job_id, mode, debug_mode: effectiveDebugMode, apply_strategy: item.apply_strategy });
      const result = await api.assistApply(item.job_id, mode, effectiveDebugMode);
      let displayedResult = result;
      setAssistResult({ jobTitle: item.title, result: normaliseAssistResult(result), diagnostic: null });
      if (result.status === "queued") {
        displayedResult = await pollAssistResult(item, result, effectiveDebugMode);
      } else {
        await refresh();
      }
      setAssistResult({ jobTitle: item.title, result: normaliseAssistResult(displayedResult), diagnostic: null });
      let diagnostic: AssistApplyDiagnosticRun | null = null;
      if (mode === "submit_with_confirmation" && submitFailed(displayedResult)) {
        diagnostic = await triggerSubmitFailureDiagnostic(item, displayedResult);
      }
      setNotice({
        type: displayedResult.submitted ? "success" : diagnostic ? "error" : "warning",
        message: displayedResult.submitted
          ? "JobServe application submitted."
          : diagnostic?.status === "passed"
            ? "Safe diagnostic passed. The failure is likely in the final form-fill/submit stage, not job lookup/browser/worker startup."
          : displayedResult.status === "queued"
            ? "Assisted apply queued. Waiting for worker diagnostics."
            : displayedResult.final_error === "stale_queue_timeout"
              ? "Worker did not pick up the assisted apply job within 2 minutes."
            : "Assisted apply diagnostics loaded. Review the debug details before taking the next action."
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to assist apply";
      if (mode === "submit_with_confirmation") {
        const failedResult = buildFailedAssistResult(message);
        const diagnostic = await triggerSubmitFailureDiagnostic(item, failedResult);
        setNotice({
          type: "error",
          message:
            diagnostic.status === "passed"
              ? "Safe diagnostic passed. The failure is likely in the final form-fill/submit stage, not job lookup/browser/worker startup."
              : "Submit failed. Safe diagnostic started."
        });
      } else {
        if (err instanceof ApiError && err.code === "worker_unavailable") {
          setNotice({ type: "error", message: "Browser automation worker is offline" });
        } else {
          setError(message);
        }
      }
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return <LoadingState label="Loading applications" />;
  }

  return (
    <div className="page-stack">
      <div className="action-row">
        <label>
          Only prepare/apply for jobs scoring at least
          <select value={threshold} onChange={(event) => void saveThreshold(Number(event.target.value))}>
            <option value={60}>60+</option>
            <option value={70}>70+</option>
            <option value={80}>80+</option>
            <option value={85}>85+</option>
            <option value={90}>90+</option>
          </select>
        </label>
        <button type="button" className="primary-button" disabled={actionLoading === "prepare"} onClick={() => void prepareApplications()}>
          {actionLoading === "prepare" ? <span className="spinner" aria-hidden="true" /> : null}
          {actionLoading === "prepare" ? "Preparing..." : "Prepare applications"}
        </button>
        <label className="inline-toggle">
          <input type="checkbox" checked={assistDebugMode} onChange={(event) => setAssistDebugMode(event.target.checked)} />
          Enable debug mode
        </label>
      </div>
      <div className={`notice-banner ${autonomousStatus?.enabled ? "warning" : "info"}`}>
        Autonomous real-submit mode: {autonomousStatus?.enabled ? "enabled" : "disabled"}. Max submits per run: {autonomousStatus?.max_submits_per_run ?? 1}.
        {autonomousStatus?.last_result ? ` Last result: ${String(autonomousStatus.last_result.status ?? "unknown")}` : " Last result: none."}
        {autonomousStatus?.enabled ? (
          <button type="button" className="secondary-button compact-button" disabled={actionLoading !== null} onClick={() => void runAutonomousRealSubmit()}>
            Run canary
          </button>
        ) : null}
      </div>
      {notice ? <div className={`notice-banner ${notice.type}`}>{notice.message}</div> : null}
      {autonomousResult ? <AutonomousOrchestrationSummary result={autonomousResult} /> : null}
      <div className="notice-banner info">Current apply threshold: {threshold}+.</div>
      {prepareRunId && prepareRun ? (
        <div className="notice-banner info">
          Preparing applications... {prepareRun.processed} / {prepareRun.total} processed, {prepareRun.queued} queued, {prepareRun.failed} failed.
        </div>
      ) : null}
      {error ? <ErrorState message={error} /> : null}
      {!error && data?.items.length === 0 ? (
        <EmptyState title="No applications queued" message="Prepare applications to queue high-scoring jobs for manual submission." />
      ) : null}
      {!error && data && data.items.length > 0 ? (
        <section className="panel">
          <div className="panel-header">
            <h2>{data.items.length} ready applications</h2>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Location</th>
                  <th>Score</th>
                  <th>Tier</th>
                  <th>Recommendation</th>
                  <th>Status</th>
                  <th>Availability</th>
                  <th>Apply route</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.job_id}>
                    <td>
                      <strong>{item.title}</strong>
                      <div className="muted-text">{item.company_name ?? "Unknown company"}</div>
                    </td>
                    <td>{item.location ?? "Not listed"}</td>
                    <td>
                      <ScoreBadge score={item.total_score} />
                    </td>
                    <td>
                      <RecommendationBadge tier={item.recommendation_tier} />
                    </td>
                    <td>
                      <RecommendationActionBadge recommendation={item.recommendation} />
                    </td>
                    <td>
                      {item.application_status.replaceAll("_", " ")}
                      {isAvailabilityCheckStale(item.last_checked_at) ? <div className="status-note">Availability check needed</div> : null}
                      {item.assisted_result?.progress?.current_step ? (
                        <div className="status-note">
                          {String(item.assisted_result.progress.current_step).replaceAll("_", " ")} · {formatElapsed(item.assisted_result.progress.elapsed_ms)}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <AvailabilityBadge status={item.availability_status} />
                      <div className="muted-text">{item.last_checked_at ? `Checked ${formatShortDate(item.last_checked_at)}` : "Not checked"}</div>
                      {item.availability_reason ? <div className="muted-text">{item.availability_reason}</div> : null}
                    </td>
                    <td>
                      <span className={`badge apply-difficulty-${item.apply_difficulty} apply-strategy-${item.apply_strategy}`}>
                        {formatLabel(item.apply_strategy)}
                      </span>
                      <div className="status-note">{formatLabel(item.apply_difficulty)}</div>
                      {item.apply_strategy_reason ? <div className="muted-text">{item.apply_strategy_reason}</div> : null}
                      {item.apply_readiness_score ? <div className="muted-text">Readiness {Math.round(Number(item.apply_readiness_score))}</div> : null}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void openApplyLink(item)}>
                          Open apply page
                        </button>
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void assistApply(item, "review_only")}>
                          Assist fill
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id || item.apply_strategy !== "jobserve_apply_easy"}
                          onClick={() => void assistApply(item, "submit_with_confirmation")}
                        >
                          Submit JobServe application
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id}
                          onClick={() => void runJobAction(item.job_id, () => api.markApplied(item.job_id), "Application marked applied.")}
                        >
                          Mark applied
                        </button>
                        <button
                          type="button"
                          className="secondary-button compact-button"
                          disabled={actionLoading === item.job_id}
                          onClick={() => void runJobAction(item.job_id, () => api.markSkipped(item.job_id), "Application skipped.")}
                        >
                          Skip
                        </button>
                        <button type="button" className="secondary-button compact-button" disabled={actionLoading === item.job_id} onClick={() => void viewScorecard(item.job_id)}>
                          View scorecard
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      {scorecard ? <ScorecardModal scorecard={scorecard} onClose={() => setScorecard(null)} /> : null}
      {assistResult ? <AssistApplyModal data={assistResult} onClose={() => setAssistResult(null)} /> : null}
    </div>
  );
}

function AssistApplyModal({ data, onClose }: { data: { jobTitle: string; result: AssistApplyResult; diagnostic?: AssistApplyDiagnosticRun | null }; onClose: () => void }) {
  const result = normaliseAssistResult(data.result);
  const diagnostic = data.diagnostic ?? null;
  const diagnosticReport = diagnostic?.final_report ?? null;
  const lastStep = result.debug_steps.at(-1) ?? {};
  const iframeCount = typeof lastStep.iframe_count === "number" ? lastStep.iframe_count : result.detected_iframes.length;
  const popupCount = typeof lastStep.popup_window_count === "number" ? lastStep.popup_window_count : null;
  return (
    <div className="modal-backdrop">
      <div className="modal-panel scorecard-modal">
        <div className="modal-header">
          <div>
            <h2>Assist apply</h2>
            <p className="muted-text">{data.jobTitle}</p>
          </div>
          <button type="button" className="secondary-button compact-button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="notice-banner warning">Review manually in the browser. The assistant did not submit the application.</div>
        {result.jobserve_flow_diagnostics?.mode === "search_to_apply" ? (
          <div className="notice-banner info">Search-to-apply flow</div>
        ) : null}
        <section className="scorecard-section">
          <h3>Filled fields</h3>
          <FieldList items={result.filled_fields} empty="No fields filled" />
        </section>
        <section className="scorecard-section">
          <h3>Unfilled fields</h3>
          <FieldList items={result.unfilled_fields} empty="No unfilled fields detected" />
        </section>
        <section className="scorecard-section">
          <h3>Required fields not filled</h3>
          <FieldList items={result.unfilled_required_fields} empty="No missing required fields reported" />
        </section>
        <section className="scorecard-section">
          <h3>Result</h3>
          <div className="metric-list">
            <Metric label="Status" value={result.status} />
            <Metric label="Running step" value={String(result.progress?.current_step ?? "Not reported")} />
            <Metric label="Elapsed" value={formatElapsed(result.progress?.elapsed_ms ?? result.timing_diagnostics?.total_runtime_ms)} />
            <Metric label="Browser startup" value={formatElapsed(result.timing_diagnostics?.browser_startup_ms)} />
            <Metric label="Waiting on JobServe" value={formatElapsed(result.timing_diagnostics?.search_page_load_ms)} />
            <Metric label="Uploading CV" value={formatElapsed(result.timing_diagnostics?.cv_upload_ms)} />
            <Metric label="Submitting application" value={formatElapsed(result.timing_diagnostics?.submit_wait_ms)} />
            <Metric label="CV uploaded" value={result.uploaded_cv ? "Yes" : "No"} />
            <Metric label="Submitted" value={result.submitted ? "Yes" : "No"} />
          </div>
        </section>
        <section className="scorecard-section">
          <h3>Warnings</h3>
          <FieldList items={result.warnings} empty="No warnings" />
        </section>
        {diagnostic ? (
          <section className="scorecard-section">
            <h3>Safe diagnostic</h3>
            {diagnostic.status === "passed" ? (
              <div className="notice-banner info">Safe diagnostic passed. The failure is likely in the final form-fill/submit stage, not job lookup/browser/worker startup.</div>
            ) : null}
            <div className="metric-list">
              <Metric label="Diagnostic status" value={diagnostic.status} />
              <Metric label="Latest progress" value={String(diagnostic.latest_progress?.phase ?? diagnostic.latest_progress?.current_step ?? "Not reported")} />
              <Metric label="Failed phase" value={String(diagnosticReport?.failed_phase ?? "None")} />
              <Metric label="Exact error" value={String(diagnosticReport?.exact_error ?? "None")} />
              <Metric label="Recommended fix" value={String(diagnosticReport?.recommended_fix ?? "None")} />
            </div>
            <DiagnosticArtifacts links={diagnostic.artifact_links} />
            <DebugJsonList title="Diagnostic report" items={diagnosticReport ? [diagnosticReport] : []} empty="No final diagnostic report yet" />
          </section>
        ) : null}
        <section className="scorecard-section">
          <h3>Debug diagnostics</h3>
          <div className="metric-list">
            <Metric label="Debug mode" value={result.debug_mode ? "Enabled" : "Disabled"} />
            <Metric label="Final URL" value={result.final_url ?? "Not reported"} />
            <Metric label="Final error" value={result.final_error ?? "None"} />
            <Metric label="Iframes" value={String(iframeCount)} />
            <Metric label="Popups/windows" value={popupCount === null ? "Not reported" : String(popupCount)} />
          </div>
          <ArtifactLinks title="Screenshots" urls={result.screenshot_urls} paths={result.screenshot_paths} image />
          <ArtifactLinks title="HTML snapshots" urls={result.html_snapshot_urls} paths={result.html_snapshot_paths} />
          <DebugJsonList title="Step timeline" items={result.debug_steps} empty="No debug steps returned" />
          <DebugJsonList title="Progress" items={objectToList(result.progress)} empty="No progress returned" />
          <DebugJsonList title="Timing diagnostics" items={objectToList(result.timing_diagnostics)} empty="No timing diagnostics returned" />
          <DebugJsonList title="JobServe flow" items={objectToList(result.jobserve_flow_diagnostics)} empty="No JobServe flow diagnostics returned" />
          <DebugJsonList title="Profile hydration" items={objectToList(result.profile_diagnostics)} empty="No profile diagnostics returned" />
          <DebugJsonList title="Upload diagnostics" items={objectToList(result.upload_diagnostics)} empty="No upload diagnostics returned" />
          <DebugJsonList title="Select diagnostics" items={result.select_diagnostics ?? []} empty="No select diagnostics returned" />
          <DebugJsonList title="Exceptions" items={result.exceptions ?? []} empty="No structured exceptions returned" />
          <DebugJsonList title="Detected buttons/links" items={result.detected_buttons} empty="No buttons or links returned" />
          <DebugJsonList title="Detected fields" items={result.detected_fields} empty="No fields returned" />
          <DebugJsonList title="Detected selects/dropdowns" items={result.detected_selects} empty="No selects returned" />
          <DebugJsonList title="Detected iframes" items={result.detected_iframes} empty="No iframe details returned" />
        </section>
      </div>
    </div>
  );
}

function AutonomousOrchestrationSummary({ result }: { result: AutonomousRealSubmitRunResult }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Autonomous orchestration summary</h2>
      </div>
      <div className="metric-list">
        <Metric label="Final outcome" value={result.status} />
        <Metric label="Failed phase" value={result.failed_phase ?? "None"} />
        <Metric label="Exact error" value={result.exact_error ?? "None"} />
        <Metric label="Recommended fix" value={result.recommended_fix} />
        <Metric label="Codex handoff" value={result.codex_handoff_status ?? "Not created"} />
        <Metric label="GitHub issue URL" value={result.github_issue_url ?? "None"} />
        <Metric label="Loop attempt" value={result.codex_handoff_attempt_count == null ? "Not reported" : String(result.codex_handoff_attempt_count)} />
      </div>
      {result.github_issue_url ? (
        <a className="artifact-link" href={result.github_issue_url} target="_blank" rel="noreferrer">
          <span>Open Codex handoff issue</span>
        </a>
      ) : null}
      <DebugJsonList title="Orchestration steps" items={result.orchestration_steps ?? []} empty="No orchestration steps returned" />
    </section>
  );
}

function DiagnosticArtifacts({ links }: { links: string[] }) {
  if (links.length === 0) {
    return (
      <div className="debug-block">
        <h4>Diagnostic artifacts</h4>
        <p className="muted-text">None returned</p>
      </div>
    );
  }
  return (
    <div className="debug-block">
      <h4>Diagnostic artifacts</h4>
      <div className="artifact-grid">
        {links.map((item, index) => {
          const isLink = item.startsWith("http") || item.startsWith("/");
          const href = item.startsWith("/") ? `${apiConfig.baseUrl}${item}` : item;
          return isLink ? (
            <a key={`${item}-${index}`} href={href} target="_blank" rel="noreferrer" className="artifact-link">
              <span>{item}</span>
            </a>
          ) : (
            <div key={`${item}-${index}`} className="artifact-link">
              <span>{item}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ArtifactLinks({ title, urls, paths, image = false }: { title: string; urls: string[]; paths: string[]; image?: boolean }) {
  const items = urls.length > 0 ? urls : paths;
  if (items.length === 0) {
    return (
      <div className="debug-block">
        <h4>{title}</h4>
        <p className="muted-text">None returned</p>
      </div>
    );
  }
  return (
    <div className="debug-block">
      <h4>{title}</h4>
      <div className="artifact-grid">
        {items.map((item, index) => {
          const href = item.startsWith("/") ? `${apiConfig.baseUrl}${item}` : item;
          return (
            <a key={`${item}-${index}`} href={href} target="_blank" rel="noreferrer" className="artifact-link">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {image ? <img src={href} alt={`${title} ${index + 1}`} /> : null}
              <span>{paths[index] ?? item}</span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

function DebugJsonList({ title, items, empty }: { title: string; items: Array<Record<string, unknown>>; empty: string }) {
  return (
    <div className="debug-block">
      <h4>{title}</h4>
      {items.length === 0 ? <p className="muted-text">{empty}</p> : null}
      {items.length > 0 ? (
        <div className="debug-json-list">
          {items.map((item, index) => (
            <pre key={index}>{JSON.stringify(item, null, 2)}</pre>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function FieldList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="muted-text">{empty}</p>;
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
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

function ScorecardModal({ scorecard, onClose }: { scorecard: JobScorecard; onClose: () => void }) {
  return (
    <div className="modal-backdrop">
      <div className="modal-panel scorecard-modal">
        <div className="modal-header">
          <div>
            <h2>Scorecard</h2>
            <p className="muted-text">{scorecard.why}</p>
          </div>
          <button type="button" className="secondary-button compact-button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="scorecard-summary">
          <div>
            <span className="muted-text">Score</span>
            <strong>{Math.round(Number(scorecard.total_score))}</strong>
          </div>
          <div>
            <span className="muted-text">Tier</span>
            <strong>{scorecard.tier}</strong>
          </div>
          <div>
            <span className="muted-text">Recommendation</span>
            <strong>{scorecard.recommendation}</strong>
          </div>
        </div>
      </div>
    </div>
  );
}

function isAvailabilityCheckStale(value: string | null): boolean {
  if (!value) {
    return true;
  }
  return Date.now() - new Date(value).getTime() > RECENT_CHECK_MS;
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(
    new Date(value)
  );
}

function formatLabel(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "unknown";
}

function emptyPrepareRun(runId: number, status: string): ApplicationPrepareRunStatus {
  return {
    run_id: runId,
    status,
    total: 0,
    processed: 0,
    queued: 0,
    skipped: 0,
    failed: 0,
    error: null,
    started_at: null,
    finished_at: null,
    last_heartbeat_at: null
  };
}

function sleep(ms: number) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function normaliseAssistResult(result: AssistApplyResult): AssistApplyResult {
  return {
    ...result,
    filled_fields: result.filled_fields ?? [],
    unfilled_fields: result.unfilled_fields ?? [],
    unfilled_required_fields: result.unfilled_required_fields ?? [],
    warnings: result.warnings ?? [],
    screenshot_paths: result.screenshot_paths ?? [],
    screenshot_urls: result.screenshot_urls ?? [],
    html_snapshot_paths: result.html_snapshot_paths ?? [],
    html_snapshot_urls: result.html_snapshot_urls ?? [],
    detected_buttons: result.detected_buttons ?? [],
    detected_fields: result.detected_fields ?? [],
    detected_selects: result.detected_selects ?? [],
    detected_iframes: result.detected_iframes ?? [],
    debug_steps: result.debug_steps ?? [],
    final_url: result.final_url ?? null,
    final_error: result.final_error ?? null,
    debug_mode: result.debug_mode ?? false,
    timing_diagnostics: result.timing_diagnostics ?? {},
    progress: result.progress ?? {},
    profile_diagnostics: result.profile_diagnostics ?? {},
    jobserve_flow_diagnostics: result.jobserve_flow_diagnostics ?? {},
    upload_diagnostics: result.upload_diagnostics ?? {},
    select_diagnostics: result.select_diagnostics ?? [],
    exceptions: result.exceptions ?? []
  };
}

function buildFailedAssistResult(message: string): AssistApplyResult {
  return normaliseAssistResult({
    status: "failed",
    filled_fields: [],
    unfilled_fields: [],
    unfilled_required_fields: [],
    uploaded_cv: false,
    submitted: false,
    warnings: [message],
    screenshot_path: null,
    screenshot_paths: [],
    screenshot_urls: [],
    html_snapshot_paths: [],
    html_snapshot_urls: [],
    detected_buttons: [],
    detected_fields: [],
    detected_selects: [],
    detected_iframes: [],
    debug_steps: [],
    final_url: null,
    final_error: message,
    debug_mode: false,
    progress: { current_step: "submit_request_failed", message }
  });
}

function submitFailed(result: AssistApplyResult): boolean {
  if (result.submitted) {
    return false;
  }
  if (result.status === "queued" || result.status === "running") {
    return false;
  }
  return result.status === "failed" || Boolean(result.final_error) || result.progress?.current_step === "stale_queue_timeout";
}

function workerProgressSeen(result: AssistApplyResult): boolean {
  const progressStep = typeof result.progress?.current_step === "string" ? result.progress.current_step : "";
  if (progressStep && progressStep !== "queued") {
    return true;
  }
  const workerSteps = new Set([
    "worker_started",
    "loading_application",
    "application_loaded",
    "loading_job",
    "job_loaded",
    "loading_profile",
    "profile_loaded",
    "resolving_job_url",
    "job_url_resolved",
    "browser_launch_start",
    "browser_launch_success",
    "worker_handoff_entered",
    "db_session_create_start",
    "db_session_create_done",
    "assist_apply_application_entered",
    "apply_button_clicked",
    "modal_wait_complete",
    "before_filling",
    "email_filled",
    "confirmation_checked",
    "working_status_selected",
    "cv_upload_started",
    "cv_uploaded",
    "final_apply_clicked",
    "submitted_message_seen",
    "account_toggle_disabled",
    "modal_closed",
    "jobserve_apply_button_clicked",
    "jobserve_apply_modal_wait_complete",
    "jobserve_apply_email_filled",
    "jobserve_apply_cv_uploaded"
  ]);
  if ((result.debug_steps ?? []).some((step) => {
    const value = typeof step.step === "string" ? step.step : "";
    return workerSteps.has(value) || value.startsWith("jobserve_");
  })) {
    return true;
  }
  const diagnostics = result.jobserve_flow_diagnostics ?? {};
  return Boolean(
    diagnostics.apply_button_clicked ||
    diagnostics.job_application_modal_found ||
    diagnostics.cv_upload_input_detected ||
    diagnostics.email_filled ||
    diagnostics.cv_uploaded
  );
}

function objectToList(value: Record<string, unknown> | undefined): Array<Record<string, unknown>> {
  if (!value || Object.keys(value).length === 0) {
    return [];
  }
  return [value];
}

function formatElapsed(value: unknown): string {
  if (typeof value !== "number") {
    return "Not reported";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${Math.round(value / 100) / 10}s`;
}
