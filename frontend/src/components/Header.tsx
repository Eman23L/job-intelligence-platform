export function Header({ title }: { title: string }) {
  return (
    <header className="header">
      <div>
        <h1>{title}</h1>
        <p>Backend dashboard MVP</p>
      </div>
    </header>
  );
}
