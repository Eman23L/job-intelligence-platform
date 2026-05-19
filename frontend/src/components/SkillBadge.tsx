export function SkillBadge({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "good" | "warn" }) {
  return <span className={`skill-badge ${tone}`}>{label}</span>;
}
