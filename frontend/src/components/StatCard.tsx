export function StatCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <section className="stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </section>
  );
}
