import React from "react";
import { Link } from "react-router-dom";

/** Klipvi mark: a wide 16:9 frame with the vertical 9:16 slice Klipvi cuts out of it. */
export const LogoMark = ({ size = 32 }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
    <rect x="1.5" y="7.5" width="29" height="17" rx="3.5" fill="none" stroke="rgb(var(--k-subtle))" strokeWidth="2" />
    <rect x="11" y="2" width="10" height="28" rx="2.5" fill="rgb(var(--k-accent))" />
    <rect x="13.5" y="5" width="5" height="2" rx="1" fill="rgb(var(--k-ink))" opacity="0.55" />
  </svg>
);

export const Logo = ({ size = "md", withText = true, className = "" }) => {
  const dim = size === "sm" ? 22 : size === "lg" ? 38 : 30;
  return (
    <Link to="/" className={`flex items-center gap-2 no-underline ${className}`} aria-label="Klipvi home">
      <LogoMark size={dim} />
      {withText && (
        <span
          className="font-display font-extrabold text-k-text"
          style={{ fontSize: size === "sm" ? 17 : size === "lg" ? 28 : 22, letterSpacing: "-0.01em", fontVariationSettings: '"wdth" 118' }}
        >
          Klip<span className="text-k-accent">vi</span>
        </span>
      )}
    </Link>
  );
};

export default Logo;
