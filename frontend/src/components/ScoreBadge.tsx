export function ScoreBadge({ score }: { score: string | number | null | undefined }) {
  if (score === null || score === undefined) {
    return <span className="badge neutral">Unscored</span>;
  }
  const value = Number(score);
  const tone = value >= 85 ? "good" : value >= 70 ? "strong" : value >= 55 ? "possible" : value >= 40 ? "stretch" : "poor";
  return <span className={`badge ${tone}`}>{Math.round(value)}</span>;
}
