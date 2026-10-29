"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, authApi, type Account } from "@/lib/api";
import { clearToken } from "@/lib/session";

const navGroups = [
  {
    label: "Job search",
    items: [
      { href: "/dashboard", label: "Overview" },
      { href: "/jobs", label: "Jobs" },
      { href: "/saved", label: "Saved" },
      { href: "/applications", label: "Applications" },
      { href: "/daily-search", label: "Daily search" }
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
      { href: "/runs", label: "Activity" },
      { href: "/account", label: "Account" }
    ]
  }
];

export function Sidebar({ account }: { account: Account | null }) {
  const pathname = usePathname();
  const [newMatches, setNewMatches] = useState(0);

  // Count of jobs waiting in New matches, refreshed on navigation and when the apply list changes.
  useEffect(() => {
    if (!account) {
      return;
    }
    let active = true;
    const load = () =>
      api
        .applicationCounts()
        .then((counts) => active && setNewMatches(counts.suggested ?? 0))
        .catch(() => undefined);
    void load();
    window.addEventListener("applications-changed", load);
    return () => {
      active = false;
      window.removeEventListener("applications-changed", load);
    };
  }, [account, pathname]);

  const signOut = async () => {
    try {
      await authApi.logout();
    } catch {
      // The token is cleared locally either way.
    }
    clearToken();
    window.location.assign("/login");
  };

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
              const active =
                pathname === item.href ||
                (item.href === "/jobs" && pathname.startsWith("/jobs/")) ||
                (item.href === "/applications" && pathname.startsWith("/apply/"));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "nav-item active" : "nav-item"}
                  aria-current={active ? "page" : undefined}
                >
                  {item.label}
                  {item.href === "/applications" && newMatches > 0 ? (
                    <span className="nav-badge" aria-label={`${newMatches} new matches`}>
                      {newMatches}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
      {account ? (
        <div className="sidebar-account">
          <span className="sidebar-account-name">{account.display_name || account.email}</span>
          <button type="button" className="ghost-button compact-button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      ) : null}
    </aside>
  );
}
