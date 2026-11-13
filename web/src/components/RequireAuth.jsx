import { Navigate } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";
import { authGate } from "@/lib/authGate";

// Route guard: everything except the landing + auth screens requires a
// session. Decision logic lives in lib/authGate.js (unit-tested there).
// While the persisted session restores, render nothing rather than flashing
// the auth screen at an already-signed-in user.
export default function RequireAuth({ children }) {
  const user = useAppStore((s) => s.user);
  const authEnabled = useAppStore((s) => s.authEnabled);
  const authLoading = useAppStore((s) => s.authLoading);

  const verdict = authGate({ user, authEnabled, authLoading });
  if (verdict === "loading") return null;
  if (verdict === "redirect") return <Navigate to="/auth" replace />;
  return children;
}
