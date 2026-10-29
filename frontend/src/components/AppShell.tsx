"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Header } from "@/components/Header";
import { LoadingState } from "@/components/LoadingState";
import { Sidebar } from "@/components/Sidebar";
import { AccountContext } from "@/lib/account-context";
import { authApi, type Account } from "@/lib/api";
import { getToken, isPublicPath, redirectToLogin } from "@/lib/session";

const pages: Record<string, { title: string; description: string }> = {
  "/dashboard": { title: "Overview", description: "Your job search at a glance." },
  "/jobs": { title: "All jobs", description: "Every job the searches have found, ranked by how well it fits your CV." },
  "/saved": { title: "Saved jobs", description: "Roles you've shortlisted and where each one stands." },
  "/applications": { title: "Applications", description: "Jobs queued for Claude, and the ones you've applied for." },
  "/find": { title: "Find jobs", description: "Jobs found for the roles you want. Apply to one, or to all of them." },
  "/ai": { title: "AI Advisor", description: "Ask questions about your matches, skills and next steps." },
  "/missing-skills": { title: "Skill gaps", description: "Skills employers ask for that aren't on your profile yet." },
  "/role-fit": { title: "Role fit", description: "How well you match each type of role." },
  "/salary": { title: "Salary", description: "What the roles you're seeing pay." },
  "/profile": { title: "My CV", description: "Your CV, the jobs you want, and the details Claude uses on applications." },
  "/sources": { title: "Sources", description: "Where jobs are collected from." },
  "/runs": { title: "Activity", description: "Background tasks like scraping, scoring and availability checks." },
  "/debug": { title: "Diagnostics", description: "Connection and configuration details." },
  "/account": { title: "Account", description: "Your sign-in details and invites." }
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const publicPage = isPublicPath(pathname);
  const [account, setAccount] = useState<Account | null>(null);
  const [checked, setChecked] = useState(false);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    if (publicPage) {
      return;
    }
    if (!getToken()) {
      redirectToLogin();
      return;
    }
    let active = true;
    // The server can take up to a minute to wake up, so say so rather than show a blank page.
    const slowTimer = window.setTimeout(() => active && setSlow(true), 2500);
    authApi
      .me()
      .then((me) => active && setAccount(me))
      .catch(() => undefined) // A 401 already redirects; anything else shouldn't block the page.
      .finally(() => {
        window.clearTimeout(slowTimer);
        if (active) {
          setChecked(true);
        }
      });
    return () => {
      active = false;
      window.clearTimeout(slowTimer);
    };
  }, [publicPage]);

  // Dropdown menus are plain <details> elements; close them on outside click or Escape.
  useEffect(() => {
    const closeMenus = (except?: Node | null) => {
      document.querySelectorAll("details.menu[open]").forEach((menu) => {
        if (!except || !menu.contains(except)) {
          menu.removeAttribute("open");
        }
      });
    };
    const onClick = (event: MouseEvent) => closeMenus(event.target as Node);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeMenus();
      }
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const page = pathname.startsWith("/jobs/")
    ? { title: "Job details", description: "" }
    : pathname.startsWith("/apply/")
      ? { title: "Apply with Claude", description: "Everything Claude needs to fill in this application for you." }
      : pages[pathname] ?? pages["/find"];

  if (publicPage) {
    return <main className="auth-page">{children}</main>;
  }
  if (!checked && !account) {
    // Hold the app until we know who is signed in, so nothing flashes before a redirect.
    return (
      <main className="auth-page" aria-busy="true">
        {slow ? <LoadingState label="Waking up the server. This can take up to a minute" /> : null}
      </main>
    );
  }

  return (
    <AccountContext.Provider value={account}>
      <div className="app-shell">
        <Sidebar account={account} />
        <div className="main-area">
          <Header title={page.title} description={page.description} />
          <main className="content">{children}</main>
        </div>
      </div>
    </AccountContext.Provider>
  );
}
