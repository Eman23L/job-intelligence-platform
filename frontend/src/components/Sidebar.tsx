"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/jobs", label: "Jobs" },
  { href: "/profile", label: "Profile" },
  { href: "/saved", label: "Saved" },
  { href: "/missing-skills", label: "Missing skills" },
  { href: "/role-fit", label: "Role fit" },
  { href: "/salary", label: "Salary" },
  { href: "/sources", label: "Sources" }
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <Link href="/dashboard" className="brand">
        <span className="brand-mark">JS</span>
        <span>
          <strong>Job Search</strong>
          <small>Intelligence</small>
        </span>
      </Link>
      <nav className="nav-list">
        {navItems.map((item) => {
          const active = pathname === item.href || (item.href === "/jobs" && pathname.startsWith("/jobs/"));
          return (
            <Link key={item.href} href={item.href} className={active ? "nav-item active" : "nav-item"}>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
