import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Mail, Lock, User, ArrowLeft, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Logo, LogoMark } from "@/components/shotvi/Logo";
import { useAppStore } from "@/store/useAppStore";
import { AUTH } from "@/constants/testIds";

export default function Auth() {
  const [tab, setTab] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const user = useAppStore((s) => s.user);
  const authEnabled = useAppStore((s) => s.authEnabled);
  const passwordRecovery = useAppStore((s) => s.passwordRecovery);
  const signInWithPassword = useAppStore((s) => s.signInWithPassword);
  const signUpWithPassword = useAppStore((s) => s.signUpWithPassword);
  const signInWithGoogle = useAppStore((s) => s.signInWithGoogle);
  const resetPassword = useAppStore((s) => s.resetPassword);
  const updatePassword = useAppStore((s) => s.updatePassword);
  const navigate = useNavigate();

  // Already signed in (session restored, OAuth redirect landed here, or dev
  // mode's permanent fake user) → straight to the app. Skipped mid password
  // recovery: that flow signs the user in but they came to set a password.
  useEffect(() => {
    if (user && !passwordRecovery) navigate("/dashboard", { replace: true });
  }, [user, passwordRecovery, navigate]);

  const run = async (fn) => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(err?.message || "Something went wrong — please try again");
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!authEnabled) return; // dev mode: form is decorative, guard is open
    run(async () => {
      if (passwordRecovery) {
        await updatePassword(password);
        toast.success("Password updated");
        navigate("/dashboard", { replace: true });
      } else if (tab === "signin") {
        await signInWithPassword(email, password);
        // navigation happens via the user effect above
      } else {
        const { needsConfirmation } = await signUpWithPassword(email, password, name);
        if (needsConfirmation) {
          setNotice("Check your inbox — confirm your email to finish creating the account.");
        }
      }
    });
  };

  const handleGoogle = () => {
    if (!authEnabled) return;
    run(() => signInWithGoogle()); // redirects away on success
  };

  const handleForgot = (e) => {
    e.preventDefault();
    if (!authEnabled) return;
    if (!email) {
      setError("Enter your email above first, then hit Forgot again.");
      return;
    }
    run(async () => {
      await resetPassword(email);
      setNotice(`Password reset link sent to ${email}.`);
    });
  };

  return (
    <div
      data-testid={AUTH.root}
      className="min-h-screen w-full bg-k-bg text-white flex relative overflow-hidden"
    >
      {/* Left: what Klipvi does, in plain words (no numbers we cannot stand behind) */}
      <div className="hidden lg:flex w-1/2 relative overflow-hidden border-r border-k-line bg-k-surface/40">
        <div className="absolute -right-24 -bottom-24 opacity-[0.07] pointer-events-none" aria-hidden="true">
          <LogoMark size={560} />
        </div>
        <div className="relative z-10 flex flex-col justify-between p-12 w-full">
          <Logo size="md" />
          <div className="max-w-md">
            <h1 className="font-display text-5xl font-extrabold leading-[1.05] mb-8">
              Your episode in. Clips ready to post, out.
            </h1>
            <ol className="space-y-5">
              {[
                "Paste a YouTube link to an episode of your web series.",
                "Klipvi watches the whole episode and finds the moments that land.",
                "Download vertical clips for Reels and Shorts, in the order to post them.",
              ].map((t, i) => (
                <li key={i} className="flex gap-4">
                  <span className="shrink-0 grid place-items-center w-8 h-8 rounded-full bg-k-accent text-k-ink font-bold">{i + 1}</span>
                  <span className="text-k-soft text-lg leading-snug pt-0.5">{t}</span>
                </li>
              ))}
            </ol>
          </div>
          <p className="text-sm text-k-subtle">Made for Telugu web series.</p>
        </div>
      </div>

      {/* Right form */}
      <div className="flex-1 flex items-center justify-center p-6 lg:p-12 relative">
        <Link
          to="/"
          data-testid={AUTH.backHome}
          className="absolute top-6 left-6 flex items-center gap-1.5 text-xs text-k-muted hover:text-white transition-colors"
        >
          <ArrowLeft size={14} /> Back home
        </Link>

        <div className="w-full max-w-md">
          <div className="lg:hidden mb-8">
            <Logo />
          </div>
          <div className="mb-8">
            <h2 className="font-display text-3xl font-bold tracking-tight mb-2">
              {passwordRecovery
                ? "Set a new password"
                : tab === "signin" ? "Welcome back" : "Create your account"}
            </h2>
            <p className="text-sm text-k-muted">
              {passwordRecovery
                ? "You followed a reset link — choose a new password below."
                : tab === "signin"
                ? "Sign in to see your episodes and clips."
                : "Start with 3 free projects. No card required."}
            </p>
          </div>

          {!authEnabled && (
            <div className="mb-6 p-3 rounded-md border border-amber-500/30 bg-amber-500/10 text-xs text-amber-200 leading-relaxed">
              Authentication isn't configured (no Supabase env vars) — running
              in <span className="font-semibold">dev mode</span>.{" "}
              <button
                type="button"
                onClick={() => navigate("/dashboard")}
                className="underline font-semibold hover:text-white"
              >
                Continue to the app
              </button>
            </div>
          )}

          {error && (
            <div
              data-testid={AUTH.error}
              className="mb-4 p-3 rounded-md border border-red-500/30 bg-red-500/10 text-xs text-red-300 leading-relaxed"
            >
              {error}
            </div>
          )}
          {notice && (
            <div
              data-testid={AUTH.notice}
              className="mb-4 p-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-xs text-emerald-200 leading-relaxed"
            >
              {notice}
            </div>
          )}

          {/* Tabs */}
          <div className={`flex p-1 bg-k-bg border border-k-line rounded-md mb-6 ${passwordRecovery ? "hidden" : ""}`}>
            <button
              data-testid={AUTH.tabSignIn}
              onClick={() => setTab("signin")}
              className={`flex-1 text-sm font-medium py-2 rounded transition-colors ${
                tab === "signin"
                  ? "bg-k-accent text-k-ink"
                  : "text-k-muted hover:text-white"
              }`}
            >
              Sign in
            </button>
            <button
              data-testid={AUTH.tabSignUp}
              onClick={() => setTab("signup")}
              className={`flex-1 text-sm font-medium py-2 rounded transition-colors ${
                tab === "signup"
                  ? "bg-k-accent text-k-ink"
                  : "text-k-muted hover:text-white"
              }`}
            >
              Sign up
            </button>
          </div>

          {!passwordRecovery && (
          <button
            data-testid={AUTH.googleBtn}
            onClick={handleGoogle}
            disabled={busy || !authEnabled}
            className="w-full flex items-center justify-center gap-2 bg-white text-black text-sm font-semibold py-2.5 rounded-md hover:bg-k-soft transition-colors mb-4 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg width="16" height="16" viewBox="0 0 24 24">
              <path
                fill="#4285F4"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
              />
              <path
                fill="#34A853"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              />
              <path
                fill="#FBBC05"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
              />
              <path
                fill="#EA4335"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              />
            </svg>
            Continue with Google
          </button>
          )}

          {!passwordRecovery && (
          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-k-line" />
            </div>
            <div className="relative flex justify-center">
              <span className="bg-k-bg px-3 text-[11px] uppercase tracking-widest text-k-subtle">
                or with email
              </span>
            </div>
          </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {tab === "signup" && !passwordRecovery && (
              <div>
                <label className="text-xs uppercase tracking-widest text-k-subtle mb-2 block">
                  Full Name
                </label>
                <div className="relative">
                  <User
                    size={15}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-k-subtle"
                  />
                  <input
                    data-testid={AUTH.name}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    type="text"
                    placeholder="Rahul Kumar"
                    className="w-full bg-k-bg border border-k-line rounded-md py-2.5 pl-10 pr-3 text-sm text-white placeholder-k-faint focus:border-k-accent focus:ring-1 focus:ring-k-accent outline-none transition-colors"
                  />
                </div>
              </div>
            )}
            <div className={passwordRecovery ? "hidden" : ""}>
              <label className="text-xs uppercase tracking-widest text-k-subtle mb-2 block">
                Email
              </label>
              <div className="relative">
                <Mail
                  size={15}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-k-subtle"
                />
                <input
                  data-testid={AUTH.email}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  type="email"
                  placeholder="you@studio.com"
                  className="w-full bg-k-bg border border-k-line rounded-md py-2.5 pl-10 pr-3 text-sm text-white placeholder-k-faint focus:border-k-accent focus:ring-1 focus:ring-k-accent outline-none transition-colors"
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs uppercase tracking-widest text-k-subtle">
                  {passwordRecovery ? "New password" : "Password"}
                </label>
                {tab === "signin" && !passwordRecovery && (
                  <a
                    href="#"
                    data-testid={AUTH.forgot}
                    onClick={handleForgot}
                    className="text-xs text-k-accentsoft hover:text-white transition-colors"
                  >
                    Forgot?
                  </a>
                )}
              </div>
              <div className="relative">
                <Lock
                  size={15}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-k-subtle"
                />
                <input
                  data-testid={AUTH.password}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  placeholder="••••••••"
                  className="w-full bg-k-bg border border-k-line rounded-md py-2.5 pl-10 pr-3 text-sm text-white placeholder-k-faint focus:border-k-accent focus:ring-1 focus:ring-k-accent outline-none transition-colors"
                />
              </div>
            </div>
            <button
              data-testid={AUTH.submit}
              type="submit"
              disabled={busy || !authEnabled}
              className="w-full flex items-center justify-center gap-2 bg-k-accent hover:bg-k-accent2 text-k-ink text-sm font-semibold py-2.5 rounded-md transition-colors shadow-[0_10px_30px_-10px_rgba(233,168,37,0.6)] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {busy
                ? "Please wait…"
                : passwordRecovery
                ? "Update password"
                : tab === "signin" ? "Sign in" : "Create account"}
              <ArrowRight size={14} />
            </button>
          </form>

          <p className="text-xs text-k-subtle text-center mt-6">
            By continuing you agree to our{" "}
            <a href="#" className="text-k-muted hover:text-white">
              Terms
            </a>{" "}
            &{" "}
            <a href="#" className="text-k-muted hover:text-white">
              Privacy Policy
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}
