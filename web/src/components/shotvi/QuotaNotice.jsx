import React from "react";
import { Zap, Loader2 } from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { QUOTA } from "@/constants/testIds";

/**
 * The single presentation of an HTTP 402 (quota / entitlement block).
 *
 * Three routes can return one — POST /jobs and POST /jobs/upload (monthly
 * SOURCE-minute budget) and the rerender route (RENDER-minute budget, premium
 * presets) — and the first two fire on PASTE, the very first thing a new user
 * does. So this is deliberately not styled as an error: amber, not red, no
 * "failed" heading, and it always carries something to click.
 *
 * `message` is the server's `detail`, rendered VERBATIM. The backend writes the
 * real numbers ("You've used 30.0 of 30 render minutes this month."); inventing
 * quota copy here would drift from the entitlement table the moment it moves.
 * This component never states a budget of its own.
 *
 * The upgrade affordance is the same `startUpgrade()` the sidebar Upgrade
 * button calls — one checkout path, and its own not-configured guard (which
 * toasts) means this stays correct on a server without Razorpay set up.
 */
export const QuotaNotice = ({
  message,
  title = "You've hit your plan's limit",
  className = "",
  children,
}) => {
  const startUpgrade = useAppStore((s) => s.startUpgrade);
  const billingActionPending = useAppStore((s) => s.billingActionPending);
  const isPaid = useAppStore((s) => s.isPaidPlan());

  return (
    <div
      data-testid={QUOTA.notice}
      className={`rounded-2xl border border-k-warn/40 bg-gradient-to-br from-k-warn/10 to-k-bg p-6 text-left ${className}`}
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 shrink-0 rounded-full bg-k-warn/20 border border-k-warn/40 flex items-center justify-center">
          <Zap size={18} className="text-k-accentsoft" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-display text-base font-semibold text-white mb-1">
            {title}
          </p>
          <p
            data-testid={QUOTA.message}
            className="text-sm text-k-soft break-words leading-relaxed"
          >
            {message}
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {/* A paid user seeing a 402 has spent their budget rather than
                lacking a plan — offering "Upgrade" there would be a dead end. */}
            {!isPaid && (
              <button
                data-testid={QUOTA.upgrade}
                onClick={startUpgrade}
                disabled={billingActionPending}
                className="inline-flex items-center gap-2 bg-k-accent hover:bg-k-accent2 disabled:opacity-60 text-k-ink text-sm font-semibold px-5 py-2.5 rounded-md transition-colors"
              >
                {billingActionPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Zap size={14} />
                )}
                {billingActionPending ? "Opening checkout…" : "Upgrade to Creator"}
              </button>
            )}
            {children}
          </div>
        </div>
      </div>
    </div>
  );
};

export default QuotaNotice;
