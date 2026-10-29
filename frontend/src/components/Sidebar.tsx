"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, authApi, type Account } from "@/lib/api";
import { clearToken } from "@/lib/session";

// The main path through the app; everything else lives under "More tools".
const mainNav = [
  { href: "/find", label: "Find jobs" },
  { href: "/applications", label: "Applications" },
  { href: "/profile", label: "My CV" },
  { href: "/account", label: "Account" }
];

const moreNav = [
  { href: "/dashboard", label: "Overview" },
  { href: "/jobs", label: "All jobs" },
  { href: "/saved", label: "Saved" },
  { href: "/ai", label: "AI Advisor" },
  { href: "/missing-skills", label: "Skill gaps" },
  { href: "/role-fit", label: "Role fit" },
  { href: "/salary", label: "Salary" },
  { href: "/sources", label: "Sources" },
  { href: "/runs", label: "Activity" }
];

export function Sidebar({ account }: { account: Account | null }) {
  const pathname = usePathname();
  const [queued, setQueued] = useState(0);

  // Count of jobs queued for Claude, refreshed on navigation and when the apply list changes.
  useEffect(() => {
    if (!account) {
      return;
    }
    let active = true;
    const load = () =>
      api
        .applicationCounts()
        .then((counts) => active && setQueued(counts.to_apply ?? 0))
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
      <Link href="/find" className="brand" aria-label="Job Search home">
        <span className="brand-mark">JS</span>
        <span>
          <strong>Job Search</strong>
          <small>Find roles that fit you</small>
        </span>
      </Link>
      <nav className="nav-list" aria-label="Main">
        <div className="nav-group">
          {mainNav.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={isActive(pathname, item.href)}
              badge={item.href === "/applications" ? queued : 0}
            />
          ))}
        </div>
        <details className="nav-more" open={moreNav.some((item) => isActive(pathname, item.href))}>
          <summary>More tools</summary>
          {moreNav.map((item) => (
            <NavLink key={item.href} item={item} active={isActive(pathname, item.href)} />
          ))}
        </details>
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

function isActive(pathname: string, href: string): boolean {
  return (
    pathname === href || (href === "/jobs" && pathname.startsWith("/jobs/")) || (href === "/applications" && pathname.startsWith("/apply/"))
  );
}

function NavLink({ item, active, badge = 0 }: { item: { href: string; label: string }; active: boolean; badge?: number }) {
  return (
    <Link href={item.href} className={active ? "nav-item active" : "nav-item"} aria-current={active ? "page" : undefined}>
      {item.label}
      {badge > 0 ? (
        <span className="nav-badge" aria-label={`${badge} queued for Claude`}>
          {badge}
        </span>
      ) : null}
    </Link>
  );
}
