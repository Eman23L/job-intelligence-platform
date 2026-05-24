import type { AvailabilityStatus } from "@/types/api";

const labels: Record<AvailabilityStatus, string> = {
  active: "Active",
  expired: "Expired",
  unavailable: "Unavailable",
  redirected: "Redirected",
  replaced: "Replaced",
  unknown: "Unknown"
};

export function AvailabilityBadge({ status }: { status: AvailabilityStatus | string | null }) {
  const value = (status || "unknown") as AvailabilityStatus;
  return <span className={`badge availability-${value}`}>{labels[value] ?? value}</span>;
}
