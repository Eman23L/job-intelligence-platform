import type { ApplicationSource, ApplicationStatus } from "@/types/api";

export const SOURCE_LABELS: Record<ApplicationSource, string> = {
  jobserve: "JobServe",
  reed: "Reed",
  adzuna: "Adzuna",
  linkedin: "LinkedIn",
  indeed: "Indeed",
  company_site: "Company website",
  other: "Other"
};

// Listings that have gone, or now show a different job.
export const CLOSED_AVAILABILITY = ["expired", "unavailable", "replaced", "redirected"];

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  to_apply: "Queued for Claude",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Not successful",
  skipped: "Skipped"
};
