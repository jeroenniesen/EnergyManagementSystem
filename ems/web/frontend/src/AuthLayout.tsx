import type { ReactNode } from "react";

// Shared shell for the three auth screens (Login/Onboarding/AcceptInvite), which render BEFORE the
// styled app shell (App.tsx's early returns, above the final <SkyBackdrop/> + <div className="app">)
// and therefore get zero CSS otherwise. Centers a card reusing the existing panel/field/button design
// tokens — no new visual language. AuthLayout owns the <h1> so each screen just passes its title.
//
// Deliberately renders on the plain theme background, with NO <SkyBackdrop/> here: SkyBackdrop
// fetches /api/sky via the authenticated apiFetch(), which 401s while logged out and (via the
// global 401 handler) clears whatever token was just issued — if login/invite-accept completes
// just before that stale in-flight 401 resolves, it wipes the brand-new session and bounces the
// user straight back to Login. Unmounting SkyBackdrop on navigation doesn't cancel that in-flight
// fetch, so the only fix is to never let the logged-out screens mount it. The authenticated App.tsx
// shell keeps its own <SkyBackdrop/> unchanged.
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
      <div className="auth-card" data-testid={testid ?? "auth-card"}>
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}
