import React, { useEffect } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutGrid,
  Upload,
  Sparkles,
  Settings,
  LogOut,
  BellDot,
  Search,
  CreditCard,
  HelpCircle,
  Check,
} from "lucide-react";
import { Logo } from "./Logo";
import { useAppStore } from "@/store/useAppStore";
import { DASHBOARD, BILLING } from "@/constants/testIds";
import { isPaidAccount, PAID_PLAN_FALLBACK_NAME } from "@/lib/plan";

const NAV = [
  { key: "dashboard", label: "Projects", icon: LayoutGrid, path: "/dashboard" },
  { key: "upload", label: "New Upload", icon: Upload, path: "/upload" },
  { key: "clips", label: "Clip Library", icon: Sparkles, path: "/clips/prj_001" },
  { key: "billing", label: "Billing", icon: CreditCard, path: "/dashboard" },
  { key: "settings", label: "Settings", icon: Settings, path: "/dashboard" },
  { key: "help", label: "Help", icon: HelpCircle, path: "/dashboard" },
];

export const AppShell = ({ children, title, subtitle, actions }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAppStore((s) => s.user);
  const signOut = useAppStore((s) => s.signOut);
  const billingStatus = useAppStore((s) => s.billingStatus);
  const billingActionPending = useAppStore((s) => s.billingActionPending);
  const loadBillingStatus = useAppStore((s) => s.loadBillingStatus);
  const startUpgrade = useAppStore((s) => s.startUpgrade);
  const cancelPlan = useAppStore((s) => s.cancelPlan);

  // Read plan state once the shell mounts (any authenticated page).
  useEffect(() => {
    loadBillingStatus();
  }, [loadBillingStatus]);

  const active = (path) => location.pathname === path;

  // Plan identity comes from the server, never from a literal in this file.
  // The purchasable key is `creator`; `studio` is the legacy alias still in
  // live Redis hashes, and isPaidAccount reads BOTH as paid — plus the
  // resolved entitlements as a fallback (see lib/plan.js). Comparing against
  // any single plan literal here would show a paying subscriber the free-tier
  // card and an Upgrade button that 409s on their live subscription.
  const isPaid = isPaidAccount(billingStatus);
  const configured = Boolean(billingStatus?.configured);
  const planName = billingStatus?.plan_info?.name || PAID_PLAN_FALLBACK_NAME;
  const planLabel = isPaid ? planName : "Free";
  const handleCancel = () => {
    if (
      window.confirm(
        `Cancel your ${planName}? You'll return to the Free plan at the end of the billing cycle.`
      )
    ) {
      cancelPlan();
    }
  };

  return (
    <div className="min-h-screen w-full text-white bg-k-bg flex">
      {/* Sidebar */}
      <aside className="w-[240px] shrink-0 border-r border-k-raised bg-k-bg flex flex-col">
        <div className="h-16 flex items-center px-5 border-b border-k-raised">
          <Logo />
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          <p className="text-[10px] uppercase tracking-[0.22em] text-k-faint px-3 mb-2">
            Workspace
          </p>
          {NAV.slice(0, 3).map((item) => {
            const Icon = item.icon;
            const isActive = active(item.path);
            return (
              <Link
                key={item.key}
                to={item.path}
                data-testid={DASHBOARD.sidebarLink(item.key)}
                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-k-accent/15 text-white border border-k-accent/30"
                    : "text-k-muted hover:text-white hover:bg-white/5 border border-transparent"
                }`}
              >
                <Icon size={16} strokeWidth={2} />
                {item.label}
              </Link>
            );
          })}

          <p className="text-[10px] uppercase tracking-[0.22em] text-k-faint px-3 mt-6 mb-2">
            Account
          </p>
          {NAV.slice(3).map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.key}
                to={item.path}
                data-testid={DASHBOARD.sidebarLink(item.key)}
                className="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-k-muted hover:text-white hover:bg-white/5 transition-colors"
              >
                <Icon size={16} strokeWidth={2} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Billing / upgrade card — reflects real plan state (PHASE 2 BUILD 2) */}
        <div
          data-testid={BILLING.card}
          className="m-3 p-4 rounded-xl border border-k-line bg-gradient-to-br from-k-accent/15 via-k-surface to-k-surface relative overflow-hidden"
        >
          <div className="absolute -top-6 -right-6 w-24 h-24 rounded-full bg-k-accent/25 blur-2xl" />
          <p className="text-xs uppercase tracking-wider text-k-accentsoft mb-1">
            {planName}
          </p>

          {isPaid ? (
            <>
              <p
                data-testid={BILLING.planStatus}
                className="text-sm font-semibold mb-2 flex items-center gap-1.5"
              >
                <Check size={15} className="text-k-ok" />
                {billingStatus?.subscription_status === "cancelling"
                  ? "Active — cancelling"
                  : "Active"}
              </p>
              <p className="text-xs text-k-muted mb-3 leading-relaxed">
                Thanks for using Klipvi. You're on the {planName}.
              </p>
              {billingStatus?.subscription_status !== "cancelling" && (
                <button
                  data-testid={BILLING.cancelButton}
                  onClick={handleCancel}
                  disabled={billingActionPending}
                  className="text-xs font-semibold text-k-muted hover:text-white border border-k-line hover:border-k-line2 px-3 py-1.5 rounded-md w-full transition-colors disabled:opacity-50"
                >
                  {billingActionPending ? "Working…" : "Cancel plan"}
                </button>
              )}
            </>
          ) : configured ? (
            <>
              <p data-testid={BILLING.planStatus} className="text-sm font-semibold mb-2">
                Upgrade to {planName}
              </p>
              <p className="text-xs text-k-muted mb-3 leading-relaxed">
                {billingStatus?.plan_info?.price_display
                  ? `${billingStatus.plan_info.price_display}, billed monthly.`
                  : "Get more minutes with a monthly plan."}
              </p>
              <button
                data-testid={BILLING.upgradeButton}
                onClick={startUpgrade}
                disabled={billingActionPending}
                className="text-xs font-semibold text-k-ink bg-k-accent hover:bg-k-accent2 px-3 py-1.5 rounded-md w-full transition-colors disabled:opacity-50"
              >
                {billingActionPending ? "Opening checkout…" : "Upgrade"}
              </button>
            </>
          ) : (
            <>
              <p data-testid={BILLING.planStatus} className="text-sm font-semibold mb-2">
                Billing not configured
              </p>
              <p className="text-xs text-k-muted leading-relaxed">
                Set up Razorpay on the server to enable upgrades (see SETUP_BILLING.md).
              </p>
            </>
          )}
        </div>

        {/* User pill */}
        <div className="border-t border-k-raised p-3 flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-k-accent to-k-mark flex items-center justify-center text-sm font-bold">
            {(user?.name?.[0] || "R").toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold truncate">
              {user?.name || "Rahul K"}
            </p>
            <p className="text-[11px] text-k-subtle truncate">
              {planLabel} · {user?.email || "creator@shotvi.app"}
            </p>
          </div>
          <button
            data-testid={DASHBOARD.userMenu}
            onClick={() => {
              signOut();
              navigate("/");
            }}
            className="p-2 rounded-md hover:bg-white/5 text-k-muted hover:text-white transition-colors"
            title="Sign out"
          >
            <LogOut size={15} />
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 flex flex-col">
        {/* Top bar */}
        <header className="h-16 border-b border-k-raised bg-k-bg/80 backdrop-blur-xl px-8 flex items-center justify-between sticky top-0 z-30">
          <div className="min-w-0">
            {title && (
              <h1 className="font-display text-xl font-semibold tracking-tight text-white truncate">
                {title}
              </h1>
            )}
            {subtitle && (
              <p className="text-xs text-k-muted mt-0.5 truncate">
                {subtitle}
              </p>
            )}
          </div>

          <div className="flex items-center gap-3">
            <div className="hidden md:flex items-center gap-2 h-9 px-3 rounded-md bg-k-surface border border-k-line w-72">
              <Search size={14} className="text-k-subtle" />
              <input
                placeholder="Search projects, clips..."
                className="bg-transparent border-0 outline-none text-sm text-white placeholder-k-subtle w-full"
              />
              <span className="text-[10px] text-k-faint px-1.5 py-0.5 rounded border border-k-line">
                ⌘K
              </span>
            </div>
            <button className="w-9 h-9 flex items-center justify-center rounded-md bg-k-surface border border-k-line text-k-muted hover:text-white transition-colors relative">
              <BellDot size={15} />
              <span className="absolute top-2 right-2 w-1.5 h-1.5 rounded-full bg-k-accent" />
            </button>
            {actions}
          </div>
        </header>

        <div className="flex-1 min-h-0">{children}</div>
      </main>
    </div>
  );
};

export default AppShell;
