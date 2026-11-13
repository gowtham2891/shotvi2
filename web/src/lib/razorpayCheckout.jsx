// Razorpay Checkout.js loader + subscription-checkout opener (PHASE 2 BUILD 2).
//
// Checkout.js is Razorpay's hosted, modal-based payment UI — no custom payment
// form, no card data ever touches our frontend. We inject its script once on
// demand (not in index.html) so the app has zero third-party payment code on
// pages that never upgrade. The store's startUpgrade() calls loadRazorpay()
// then openSubscriptionCheckout(); both are mocked in unit tests.

const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

let loadPromise = null;

// Resolves once window.Razorpay is available. Idempotent: concurrent callers
// share one <script> injection; a failed load clears the cache so a later
// attempt can retry.
export function loadRazorpay() {
  if (typeof window !== "undefined" && window.Razorpay) return Promise.resolve(window.Razorpay);
  if (loadPromise) return loadPromise;

  loadPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${CHECKOUT_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(window.Razorpay));
      existing.addEventListener("error", () => {
        loadPromise = null;
        reject(new Error("Failed to load Razorpay Checkout"));
      });
      if (window.Razorpay) resolve(window.Razorpay);
      return;
    }
    const script = document.createElement("script");
    script.src = CHECKOUT_SRC;
    script.async = true;
    script.onload = () => resolve(window.Razorpay);
    script.onerror = () => {
      loadPromise = null;
      reject(new Error("Failed to load Razorpay Checkout"));
    };
    document.body.appendChild(script);
  });
  return loadPromise;
}

// Open the subscription checkout modal. `onSuccess` fires when the user
// completes payment (authorization); `onDismiss` when they close the modal
// without paying. The webhook — not this callback — is the authoritative
// source of paid status; onSuccess just triggers a client-side status refetch.
export function openSubscriptionCheckout({
  keyId,
  subscriptionId,
  name = "Klipvi",
  // Fallback only — callers pass the server's plan_info.name.
  description = "Creator Plan",
  email = "",
  themeColor = "#E9A825",
  onSuccess,
  onDismiss,
} = {}) {
  const Razorpay = typeof window !== "undefined" ? window.Razorpay : null;
  if (!Razorpay) throw new Error("Razorpay Checkout is not loaded");

  const rzp = new Razorpay({
    key: keyId,
    subscription_id: subscriptionId,
    name,
    description,
    prefill: email ? { email } : undefined,
    theme: { color: themeColor },
    handler: (response) => {
      if (typeof onSuccess === "function") onSuccess(response);
    },
    modal: {
      ondismiss: () => {
        if (typeof onDismiss === "function") onDismiss();
      },
    },
  });
  // Surface payment failures (distinct from a plain dismiss) without breaking
  // the app — the modal handles its own retry UI; we just don't want an
  // uncaught event.
  if (typeof rzp.on === "function") {
    rzp.on("payment.failed", () => {
      if (typeof onDismiss === "function") onDismiss();
    });
  }
  rzp.open();
  return rzp;
}
