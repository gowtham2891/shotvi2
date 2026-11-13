import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useAppStore } from "@/store/useAppStore";
import { getCaptionFontStack } from "@/data/captionStylePreview";
import {
  WATERMARK_TEXT,
  WATERMARK_OPACITY,
  WATERMARK_SHADOW_ALPHA,
  watermarkPreviewLayout,
} from "@/lib/watermark";
import { EDITOR } from "@/constants/testIds";

/**
 * PreviewWatermark — the free tier's burned-in wordmark, shown live on the
 * canvas while editing instead of being discovered after download.
 *
 * NOT AN ELEMENT, on purpose. It never enters the store's `elements` array, so
 * it cannot be dragged, selected, hidden, deleted, undone, autosaved into a
 * draft, or listed in the layers panel — and, the reason that actually matters,
 * it never serializes into the rerender payload. If it were an element, a
 * crafted payload could simply omit it. The authority stays server-side:
 * api/main.py resolves `watermark = tiers.has_watermark(plan)` and
 * services/overlay_renderer.py injects the layer inside render_elements. This
 * component only PREVIEWS that decision; it can never change it.
 *
 * Geometry mirrors the burn through lib/watermark.js — see that file and
 * services/overlay_renderer.py's WATERMARK_* block.
 *
 * The mark doubles as the upgrade affordance: hovering reveals "Remove
 * watermark", clicking calls the same startUpgrade() the sidebar and the
 * Export page's CTA use. That turns the one piece of friction free users see
 * on every clip into the conversion surface.
 *
 * canvasH: preview box height in CSS px. Omitted (the editor canvas mounts it
 * without props) → measured from its own inset-0 box, which is layout height
 * and therefore immune to the stage's scale(zoom) transform, the same
 * offsetHeight trick ElementRenderer documents.
 */
export const PreviewWatermark = ({ canvasH: canvasHProp }) => {
  const aspect = useAppStore((s) => s.exportSettings.format);
  const billingStatus = useAppStore((s) => s.billingStatus);
  const isPaid = useAppStore((s) => s.isPaidPlan());
  const loadBillingStatus = useAppStore((s) => s.loadBillingStatus);
  const startUpgrade = useAppStore((s) => s.startUpgrade);
  const billingActionPending = useAppStore((s) => s.billingActionPending);

  const boxRef = useRef(null);
  const [measuredH, setMeasuredH] = useState(0);
  const [hovered, setHovered] = useState(false);

  // The editor route is not inside AppShell, so nothing else has fetched
  // billing status by the time the canvas mounts. Same pattern as Export.jsx.
  useEffect(() => {
    if (!billingStatus) loadBillingStatus();
  }, [billingStatus, loadBillingStatus]);

  useLayoutEffect(() => {
    if (canvasHProp) return undefined;
    const el = boxRef.current;
    if (!el) return undefined;
    const measure = () => setMeasuredH(el.offsetHeight || 0);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [canvasHProp, aspect]);

  // Show ONLY once we know the account is free. While billingStatus is still
  // null we render nothing: lib/plan.js spells out why that asymmetry matters
  // — flashing a watermark at a paying subscriber is the worse error.
  if (!billingStatus || isPaid) {
    // The measuring box still has to exist for the un-prop'd (editor) mount,
    // otherwise there is nothing to measure when the plan resolves.
    return canvasHProp ? null : (
      <div ref={boxRef} className="absolute inset-0 pointer-events-none" aria-hidden />
    );
  }

  const canvasH = canvasHProp || measuredH;
  const layout = watermarkPreviewLayout(aspect, canvasH);

  return (
    <div ref={boxRef} className="absolute inset-0 pointer-events-none z-[55]">
      <button
        type="button"
        data-testid={EDITOR.watermark}
        title="Remove watermark — upgrade"
        disabled={billingActionPending}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          startUpgrade();
        }}
        className="absolute pointer-events-auto cursor-pointer bg-transparent border-0 p-0 leading-none"
        style={{
          top: layout.top,
          right: layout.right,
          fontFamily: getCaptionFontStack("Montserrat"),
          // The bundled Montserrat-Black.ttf is registered @font-face at
          // weight 400 (public/fonts.css) — asking for 900 here would trigger
          // faux-bold synthesis on top of already-Black outlines.
          fontWeight: 400,
          fontSize: layout.fontSize,
          lineHeight: 1,
          color: `rgba(255,255,255,${WATERMARK_OPACITY})`,
          textShadow: `0 ${layout.shadowOffset}px ${layout.shadowBlur}px rgba(0,0,0,${WATERMARK_SHADOW_ALPHA})`,
          whiteSpace: "nowrap",
        }}
      >
        {WATERMARK_TEXT}
      </button>

      {/* Upgrade affordance — the mark IS the CTA. Sits directly beneath it,
          right-aligned to the same inset so it never crosses the frame edge. */}
      {hovered && (
        <button
          type="button"
          data-testid={EDITOR.watermarkUpgrade}
          disabled={billingActionPending}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            startUpgrade();
          }}
          className="absolute pointer-events-auto cursor-pointer whitespace-nowrap rounded-md border border-k-ok/40 bg-black/80 px-2 py-1 text-[10px] font-semibold text-k-ok backdrop-blur-sm"
          style={{
            right: layout.right,
            top: layout.top + layout.fontSize * 1.25,
          }}
        >
          Remove watermark — upgrade
        </button>
      )}
    </div>
  );
};

export default PreviewWatermark;
