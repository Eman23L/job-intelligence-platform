"use client";

import { createContext, useContext } from "react";
import type { Account } from "@/lib/api";

export const AccountContext = createContext<Account | null>(null);

export function useAccount(): Account | null {
  return useContext(AccountContext);
}

// Shared data (job sources, deleting or hiding jobs for everyone) is managed by the owner only.
export function useIsOwner(): boolean {
  return Boolean(useContext(AccountContext)?.is_admin);
}
