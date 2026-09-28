export function Header({ title, description }: { title: string; description?: string }) {
  return (
    <header className="header">
      <h1>{title}</h1>
      {description ? <p>{description}</p> : null}
    </header>
  );
}
