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
  const [error, setError] = useState<string | null>(null);

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
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [params]);

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
            <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
          </div>
          <JobsTable jobs={data.items} />
          <PaginationControls page={data.page} totalPages={data.total_pages} onPageChange={setPage} />
        </section>
      ) : null}
    </div>
  );
}
