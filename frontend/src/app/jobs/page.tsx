"use client";

import { useEffect, useMemo, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FiltersBar, type JobFiltersState } from "@/components/FiltersBar";
import { JobsTable } from "@/components/JobsTable";
import { LoadingState } from "@/components/LoadingState";
import { PaginationControls } from "@/components/PaginationControls";
import { api } from "@/lib/api";
import type { PaginatedJobs } from "@/types/api";

const initialFilters: JobFiltersState = {
  role_family: "",
  recommendation_tier: "",
  remote_type: "",
  location: "",
  company_name: "",
  min_score: "",
  max_score: "",
  exclude_excluded: false,
  sort: "total_score_desc"
};

export default function JobsPage() {
  const [filters, setFilters] = useState<JobFiltersState>(initialFilters);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<PaginatedJobs | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const params = useMemo(() => {
    const next = new URLSearchParams();
    next.set("page", String(page));
    next.set("page_size", "20");
    next.set("sort", filters.sort);
    Object.entries(filters).forEach(([key, value]) => {
      if (key === "sort") {
        return;
      }
      if (typeof value === "boolean") {
        if (value) {
          next.set(key, "true");
        }
      } else if (value.trim()) {
        next.set(key, value.trim());
      }
    });
    return next;
  }, [filters, page]);

  useEffect(() => {
    setLoading(true);
    api
      .jobs(params)
      .then((result) => {
        setData(result);
        setError(null);
        setSelectedIds(new Set());
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [params]);

  const refresh = async () => {
    const result = await api.jobs(params);
    setData(result);
    setSelectedIds(new Set());
  };

  const runAction = async (action: () => Promise<unknown>) => {
    setActionLoading(true);
    try {
      await action();
      await refresh();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Job cleanup action failed");
    } finally {
      setActionLoading(false);
    }
  };

  const selectedJobIds = Array.from(selectedIds);
  const selectedCount = selectedJobIds.length;

  const deleteJobs = (jobIds: number[]) => {
    const message =
      jobIds.length === 1
        ? "Permanently delete this job and its related analysis, scores, skills, missing skills, and saved/applied records?"
        : `Permanently delete ${jobIds.length} jobs and their related analysis, scores, skills, missing skills, and saved/applied records?`;
    if (!window.confirm(message)) {
      return;
    }
    void runAction(() => (jobIds.length === 1 ? api.deleteJob(jobIds[0]) : api.bulkDeleteJobs(jobIds)));
  };

  const excludeJobs = (jobIds: number[]) => {
    void runAction(() => api.bulkExcludeJobs(jobIds));
  };

  return (
    <div className="page-stack">
      <FiltersBar
        filters={filters}
        onChange={(next) => {
          setFilters(next);
          setPage(1);
        }}
        onReset={() => {
          setFilters(initialFilters);
          setPage(1);
        }}
      />
      {error ? <ErrorState message={error} /> : null}
      {loading ? <LoadingState label="Loading jobs" /> : null}
      {!loading && !error && data?.items.length === 0 ? (
        <EmptyState title="No jobs found" message="Adjust the filters or clear the search criteria." />
      ) : null}
      {!loading && !error && data && data.items.length > 0 ? (
        <section className="panel">
          <div className="panel-header">
            <h2>{data.total_count} jobs</h2>
            <div className="panel-actions">
              <span className="muted-text">{selectedCount} selected</span>
              <button type="button" className="secondary-button" disabled={selectedCount === 0 || actionLoading} onClick={() => excludeJobs(selectedJobIds)}>
                Exclude selected
              </button>
              <button type="button" className="danger-button" disabled={selectedCount === 0 || actionLoading} onClick={() => deleteJobs(selectedJobIds)}>
                Delete selected
              </button>
              <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
            </div>
          </div>
          <JobsTable
            jobs={data.items}
            selectedIds={selectedIds}
            onToggle={(jobId, checked) => {
              setSelectedIds((current) => {
                const next = new Set(current);
                if (checked) {
                  next.add(jobId);
                } else {
                  next.delete(jobId);
                }
                return next;
              });
            }}
            onToggleAll={(checked) => {
              setSelectedIds((current) => {
                const next = new Set(current);
                data.items.forEach((job) => {
                  if (checked) {
                    next.add(job.id);
                  } else {
                    next.delete(job.id);
                  }
                });
                return next;
              });
            }}
            onDelete={(jobId) => deleteJobs([jobId])}
            onExclude={(jobId) => excludeJobs([jobId])}
          />
          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
        </section>
      ) : null}
    </div>
  );
}
