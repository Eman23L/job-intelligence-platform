export function ErrorState({ message }: { message: string }) {
  return (
    <div className="state-card error">
      <strong>Unable to load data</strong>
      <p>{message}</p>
    </div>
  );
}
