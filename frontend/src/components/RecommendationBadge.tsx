export function RecommendationBadge({ tier }: { tier: string | null | undefined }) {
  if (!tier) {
    return <span className="badge neutral">No tier</span>;
  }
  const key = tier.toLowerCase().replaceAll(" ", "-");
  return <span className={`badge ${key}`}>{tier}</span>;
}
