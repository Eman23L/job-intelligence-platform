"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";

const titles: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/jobs": "Jobs",
  "/profile": "Profile",
  "/saved": "Saved jobs",
  "/missing-skills": "Missing skills",
  "/role-fit": "Role fit",
  "/salary": "Salary",
  "/sources": "Sources"
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const title = pathname.startsWith("/jobs/") ? "Job detail" : titles[pathname] ?? "Dashboard";

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-area">
        <Header title={title} />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
