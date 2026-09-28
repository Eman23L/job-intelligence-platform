"use client";

import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";

const pages: Record<string, { title: string; description: string }> = {
  "/dashboard": { title: "Overview", description: "Your job search at a glance." },
  "/jobs": { title: "Jobs", description: "Every role we've found, ranked by how well it fits your profile." },
  "/saved": { title: "Saved jobs", description: "Roles you've shortlisted and where each one stands." },
  "/applications": { title: "Applications", description: "Prepare and track applications for your best matches." },
  "/ai": { title: "AI Advisor", description: "Ask questions about your matches, skills and next steps." },
  "/missing-skills": { title: "Skill gaps", description: "Skills employers ask for that aren't on your profile yet." },
  "/role-fit": { title: "Role fit", description: "How well you match each type of role." },
  "/salary": { title: "Salary", description: "What the roles you're seeing pay." },
  "/profile": { title: "Profile", description: "Your CV and application details, used to score every job." },
  "/sources": { title: "Sources", description: "Where jobs are collected from." },
  "/runs": { title: "Activity", description: "Background tasks like scraping, scoring and availability checks." },
  "/debug": { title: "Diagnostics", description: "Connection and configuration details." }
};

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

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
    : pages[pathname] ?? pages["/dashboard"];

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-area">
        <Header title={page.title} description={page.description} />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
