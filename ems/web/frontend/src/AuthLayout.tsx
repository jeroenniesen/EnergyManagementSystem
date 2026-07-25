import type { ReactNode } from "react";

import { SkyBackdrop } from "./SkyBackdrop";

// Shared shell for the three auth screens (Login/Onboarding/AcceptInvite), which render BEFORE the
// styled app shell (App.tsx's early returns, above the final <SkyBackdrop/> + <div className="app">)
// and therefore get zero CSS otherwise. Centers a card reusing the existing panel/field/button design
// tokens — no new visual language. AuthLayout owns the <h1> so each screen just passes its title.
export function AuthLayout({
  title,
  testid,
  children,
}: {
  title: string;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <div className="auth-screen">
      <SkyBackdrop compact />
      <div className="auth-card" data-testid={testid ?? "auth-card"}>
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}
