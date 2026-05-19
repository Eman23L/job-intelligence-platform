export function LoadingState({ label = "Loading data" }: { label?: string }) {
  return (
    <div className="state-card loading-state" role="status" aria-live="polite">
      <span className="loading-dot" />
      <span>{label}...</span>
    </div>
  );
}
