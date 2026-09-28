"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navGroups = [
  {
    label: "Job search",
    items: [
      { href: "/dashboard", label: "Overview" },
      { href: "/jobs", label: "Jobs" },
      { href: "/saved", label: "Saved" },
      { href: "/applications", label: "Applications" }
    ]
  },
  {
    label: "Insights",
    items: [
      { href: "/ai", label: "AI Advisor" },
      { href: "/missing-skills", label: "Skill gaps" },
      { href: "/role-fit", label: "Role fit" },
      { href: "/salary", label: "Salary" }
    ]
  },
  {
    label: "Settings",
    items: [
      { href: "/profile", label: "Profile" },
      { href: "/sources", label: "Sources" },
      { href: "/runs", label: "Activity" }
    ]
  }
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link href="/dashboard" className="brand" aria-label="Job Search home">
        <span className="brand-mark">JS</span>
        <span>
          <strong>Job Search</strong>
          <small>Find roles that fit you</small>
        </span>
      </Link>
      <nav className="nav-list" aria-label="Main">
        {navGroups.map((group) => (
          <div key={group.label} className="nav-group">
            <span className="nav-group-label">{group.label}</span>
            {group.items.map((item) => {
              const active = pathname === item.href || (item.href === "/jobs" && pathname.startsWith("/jobs/"));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "nav-item active" : "nav-item"}
                  aria-current={active ? "page" : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
