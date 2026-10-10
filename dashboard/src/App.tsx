import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { Dashboard } from "./pages/Dashboard";
import { LoginPage } from "./pages/LoginPage";
import { authApi } from "./api/client";
import { clearSession, currentSession, SessionUser, SIGNED_OUT_EVENT } from "./api/auth";

const queryClient = new QueryClient();

function Gate() {
  const { data: config, isLoading } = useQuery({ queryKey: ["auth-config"], queryFn: authApi.config, staleTime: Infinity });
  const [user, setUser] = useState<SessionUser | null>(() => currentSession()?.user ?? null);
  const [signingIn, setSigningIn] = useState(false);

  useEffect(() => {
    const onSignedOut = () => {
      setUser(null);
      queryClient.clear(); // nothing read with the old session stays on screen
    };
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, []);

  if (isLoading || !config) return <div className="min-h-screen bg-slate-950" />;

  const mustSignIn = !config.public_dashboard && user === null;
  if (mustSignIn || signingIn) {
    return (
      <LoginPage
        onSignedIn={(u) => {
          queryClient.clear();
          setUser(u);
          setSigningIn(false);
        }}
        onCancel={mustSignIn ? undefined : () => setSigningIn(false)}
      />
    );
  }

  return (
    <Dashboard
      user={user}
      onSignIn={config.login ? () => setSigningIn(true) : undefined}
      onSignOut={async () => {
        await authApi.logout();
        clearSession();
      }}
    />
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Gate />
    </QueryClientProvider>
  );
}
