export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="state-card">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}
