export function RecommendationActionBadge({ recommendation }: { recommendation: string | null | undefined }) {
  if (!recommendation) {
    return <span className="badge neutral">Not scored</span>;
  }
  const key = recommendation.toLowerCase();
  const label = key.charAt(0).toUpperCase() + key.slice(1);
  return <span className={`badge recommendation-${key}`}>{label}</span>;
}
