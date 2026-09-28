import type { ReactNode } from "react";

export interface BarItem {
  key: string;
  label: ReactNode;
  detail?: ReactNode;
  value: ReactNode;
  // Portion of the track to fill, 0-1. Use start to draw a range (e.g. salary min-max).
  fraction: number;
  start?: number;
  tone?: "accent" | "good" | "warn" | "danger" | "neutral";
}

export function BarList({ items }: { items: BarItem[] }) {
  return (
    <div className="bar-list">
      {items.map((item) => {
        const start = clamp(item.start ?? 0);
        const end = Math.max(start, clamp(item.fraction));
        return (
          <div key={item.key} className="bar-row">
            <div className="bar-row-text">
              <span className="bar-label">
                {item.label}
                {item.detail ? <span className="muted-text"> {item.detail}</span> : null}
              </span>
              <strong>{item.value}</strong>
            </div>
            <div className="bar-track" aria-hidden="true">
              <span
                className={`bar-fill ${item.tone ?? "accent"}`}
                style={{ left: `${start * 100}%`, width: `${Math.max((end - start) * 100, 1.5)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function clamp(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}
