export function formatDate(value: string | null | undefined) {
  if (!value) {
    return "Not listed";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Not listed";
  }
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(date);
}

export function formatNumber(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) {
    return "Not available";
  }
  return new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 }).format(Number(value));
}

export function formatSalary(min: string | null | undefined, max: string | null | undefined, currency: string | null | undefined = "GBP") {
  if (!min && !max) {
    return "Not listed";
  }
  const formatter = currencyFormatter(currency);
  if (min && max) {
    return `${formatter.format(Number(min))} - ${formatter.format(Number(max))}`;
  }
  return formatter.format(Number(min || max));
}

export function formatSalaryPeriod(period: string | null | undefined) {
  if (period === "day") {
    return "day";
  }
  if (period === "hour") {
    return "hour";
  }
  if (period === "year") {
    return "year";
  }
  return null;
}

function currencyFormatter(currency: string | null | undefined) {
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency: currency || "GBP", maximumFractionDigits: 0 });
  } catch {
    // Unknown ISO code from a scraped listing; fall back rather than crash the page.
    return new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 });
  }
}
