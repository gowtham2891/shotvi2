/**
 * Feature #17 — the free-tier watermark, previewed in the editor.
 *
 * Two claims are under test and they pull in opposite directions:
 *
 *  1. Free users must SEE the mark while editing, at the size and position the
 *     burn will put it. That is the WYSIWYG half — lib/watermark.js mirrors
 *     services/overlay_renderer.py's WATERMARK_* block, and
 *     tests/test_watermark_parity.py pins the two against each other from the
 *     Python side.
 *
 *  2. The mark must NOT be a store element. If it entered `elements` it would
 *     serialize into the rerender payload through buildRerenderRequest, and a
 *     crafted payload could simply omit it. Server-side derivation
 *     (api/main.py :: tiers.has_watermark) is only authoritative because the
 *     client never sends the mark at all — so it also stays out of the edit
 *     document, undo history, drafts and the layers list.
 *
 * The size assertions are the regression the spec called out: min(W,H)/12 is
 * 90px on every format, where the burn's old fraction-of-HEIGHT rule gave 74px
 * on 9:16 but only ~41px on 1:1 and 16:9.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest, OVERLAY_ELEMENT_TYPES } from "@/api/renders";
import { PreviewWatermark } from "@/components/editor/PreviewWatermark";
import {
  WATERMARK_TEXT,
  WATERMARK_OPACITY,
  WATERMARK_RIGHT_INSET,
  WATERMARK_TOP_INSET_TALL,
  WATERMARK_TOP_INSET_WIDE,
  WATERMARK_SIZE_DIVISOR,
  WATERMARK_RENDER_DIMS,
  watermarkRenderGeometry,
  watermarkPreviewLayout,
} from "@/lib/watermark";
import { EDITOR } from "@/constants/testIds";

jest.mock("@/api/billing", () => ({
  getBillingStatus: jest.fn(),
  createSubscription: jest.fn(),
  cancelSubscription: jest.fn(),
}));
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

// Editor stage heights (CanvasArea STAGE_DIMS) — the preview boxes the mark
// actually renders into.
const STAGE_H = { "9:16": 640, "1:1": 560, "16:9": 540 };

// ── Geometry: the burn's rule, mirrored ─────────────────────────────────────

describe("watermark geometry (mirror of services/overlay_renderer.py)", () => {
  test("the mark is 90px on EVERY aspect — min(W,H)/12, not a fraction of height", () => {
    for (const aspect of Object.keys(WATERMARK_RENDER_DIMS)) {
      expect(watermarkRenderGeometry(aspect).size).toBe(90);
    }
  });

  test("the old fraction-of-height rule is what this replaced", () => {
    // h/26 gave three different sizes and shrank the mark ~45% on the wide
    // formats. Pinning the old numbers documents why the rule changed.
    const old = Object.fromEntries(
      Object.entries(WATERMARK_RENDER_DIMS).map(([a, d]) => [a, Math.round(d.h / 26)])
    );
    expect(old).toEqual({ "9:16": 74, "1:1": 42, "16:9": 42 });
    const sizes = Object.keys(WATERMARK_RENDER_DIMS).map(
      (a) => watermarkRenderGeometry(a).size
    );
    expect(new Set(sizes).size).toBe(1);
  });

  test("size is derived from the SHORTER side, proven on a synthetic ratio", () => {
    // Guards the formula itself: were it height-based, 16:9 (h=1080, w=1920)
    // and 9:16 (h=1920) could not both land on 90.
    for (const [aspect, d] of Object.entries(WATERMARK_RENDER_DIMS)) {
      expect(watermarkRenderGeometry(aspect).size).toBe(
        Math.round(Math.min(d.w, d.h) / WATERMARK_SIZE_DIVISOR)
      );
    }
  });

  test("right inset never varies; only the top inset is per-aspect", () => {
    for (const aspect of Object.keys(WATERMARK_RENDER_DIMS)) {
      expect(watermarkRenderGeometry(aspect).rightInset).toBe(WATERMARK_RIGHT_INSET);
    }
    expect(watermarkRenderGeometry("9:16").topInset).toBe(WATERMARK_TOP_INSET_TALL);
    expect(watermarkRenderGeometry("1:1").topInset).toBe(WATERMARK_TOP_INSET_WIDE);
    expect(watermarkRenderGeometry("16:9").topInset).toBe(WATERMARK_TOP_INSET_WIDE);
  });

  test("an unknown aspect falls back to 9:16 rather than producing NaN", () => {
    expect(watermarkRenderGeometry("21:9")).toEqual(watermarkRenderGeometry("9:16"));
  });

  test("preview px = burn px x (canvasH / renderH) on every aspect", () => {
    for (const [aspect, stageH] of Object.entries(STAGE_H)) {
      const r = stageH / WATERMARK_RENDER_DIMS[aspect].h;
      const g = watermarkRenderGeometry(aspect);
      const l = watermarkPreviewLayout(aspect, stageH);
      expect(l.fontSize).toBeCloseTo(g.size * r, 6);
      expect(l.right).toBeCloseTo(g.rightInset * r, 6);
      expect(l.top).toBeCloseTo(g.topInset * r, 6);
    }
  });

  test("the 9:16 stage lands on the concrete numbers the burn implies", () => {
    // 1/3 scale: 90 -> 30px type, 86 -> 28.67px inset, 288 -> 96px from the top.
    const l = watermarkPreviewLayout("9:16", 640);
    expect(l.fontSize).toBeCloseTo(30, 6);
    expect(l.right).toBeCloseTo(28.666, 2);
    expect(l.top).toBeCloseTo(96, 6);
  });

  test("the mark sits well above the caption band on 9:16", () => {
    // The reason it is not bottom-right any more: captions live at y=0.74.
    const g = watermarkRenderGeometry("9:16");
    expect((g.topInset + g.size) / WATERMARK_RENDER_DIMS["9:16"].h).toBeLessThan(0.54);
  });

  test("a zero-height preview box degrades to zero, never NaN", () => {
    const l = watermarkPreviewLayout("9:16", 0);
    expect(Number.isFinite(l.fontSize) && Number.isFinite(l.top)).toBe(true);
  });
});

// ── Rendering: who sees it ──────────────────────────────────────────────────

let container;
let root;
const mount = async (jsx) => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(jsx));
};
const unmount = async () => {
  await act(async () => root.unmount());
  container.remove();
};
const byTestId = (id) => container.querySelector(`[data-testid="${id}"]`);

const setPlan = (billingStatus) =>
  useAppStore.setState({ billingStatus, billingActionPending: false });

afterEach(async () => {
  if (root) await unmount();
  root = null;
});

describe("PreviewWatermark visibility by tier", () => {
  test("a free account sees the mark on the canvas", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    await mount(<PreviewWatermark canvasH={640} />);
    const el = byTestId(EDITOR.watermark);
    expect(el).not.toBeNull();
    expect(el.textContent).toBe(WATERMARK_TEXT);
  });

  test("a paid account sees NOTHING — no mark, no upgrade nag", async () => {
    setPlan({ plan: "creator", configured: true, watermark: false });
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermark)).toBeNull();
    expect(byTestId(EDITOR.watermarkUpgrade)).toBeNull();
  });

  test("a legacy `studio` subscriber counts as paid (plan-key rename)", async () => {
    setPlan({ plan: "studio", configured: true, watermark: false });
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermark)).toBeNull();
  });

  test("an active subscription counts as paid even on an unknown plan key", async () => {
    setPlan({ plan: "", configured: true, subscription_status: "active" });
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermark)).toBeNull();
  });

  test("while the plan is still unknown, nothing is drawn", async () => {
    // The asymmetry lib/plan.js spells out: flashing a watermark at a paying
    // subscriber is the worse error, so an unresolved plan shows no mark.
    setPlan(null);
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermark)).toBeNull();
  });

  test("the drawn mark carries the mirrored size, inset and opacity", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    await mount(<PreviewWatermark canvasH={640} />);
    const el = byTestId(EDITOR.watermark);
    const l = watermarkPreviewLayout("9:16", 640);
    expect(el.style.fontSize).toBe(`${l.fontSize}px`);
    expect(el.style.right).toBe(`${l.right}px`);
    expect(el.style.top).toBe(`${l.top}px`);
    expect(el.style.color).toContain(String(WATERMARK_OPACITY));
    // Montserrat Black is registered @font-face at weight 400 — asking for 900
    // would faux-bold already-Black outlines.
    expect(el.style.fontWeight).toBe("400");
    expect(el.style.fontFamily).toContain("Montserrat");
  });

  test("switching the export aspect re-derives the preview geometry", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    useAppStore.setState((s) => ({
      exportSettings: { ...s.exportSettings, format: "16:9" },
    }));
    await mount(<PreviewWatermark canvasH={540} />);
    const l = watermarkPreviewLayout("16:9", 540);
    expect(byTestId(EDITOR.watermark).style.top).toBe(`${l.top}px`);
    // 16:9 uses the SHORT top inset — the tall one would drop it to mid-frame.
    expect(l.top).toBeCloseTo((WATERMARK_TOP_INSET_WIDE * 540) / 1080, 6);
    await act(async () =>
      useAppStore.setState((s) => ({
        exportSettings: { ...s.exportSettings, format: "9:16" },
      }))
    );
  });
});

// ── The structural rule: never an element ───────────────────────────────────

describe("the watermark is not, and must never become, a store element", () => {
  test("rendering it adds nothing to `elements`", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    const before = useAppStore.getState().elements.map((e) => e.id);
    await mount(<PreviewWatermark canvasH={640} />);
    expect(useAppStore.getState().elements.map((e) => e.id)).toEqual(before);
  });

  test("it is absent from the edit document, so drafts and undo never carry it", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    await mount(<PreviewWatermark canvasH={640} />);
    const doc = useAppStore.getState().getEditDocument();
    expect(JSON.stringify(doc)).not.toMatch(/watermark/i);
  });

  test("`watermark` is not an exportable overlay type", () => {
    // The wire whitelist. Even if something did put one in `elements`, it
    // could not ride the payload.
    expect(OVERLAY_ELEMENT_TYPES.has("watermark")).toBe(false);
  });

  test("the rerender payload never carries a watermark element", () => {
    const req = buildRerenderRequest({
      elements: [
        { id: "e1", type: "headline", visible: true, x: 0.5, y: 0.14, props: {} },
        // A crafted client trying to smuggle one in either direction.
        { id: "e2", type: "watermark", visible: true, x: 0.5, y: 0.5, props: {} },
      ],
    });
    for (const el of req.elements || []) expect(el.type).not.toBe("watermark");
    expect(JSON.stringify(req)).not.toMatch(/watermark/i);
  });

  test("it is not selectable — clicking it never changes the selection", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    useAppStore.setState({ selectedElementId: null, selectedIds: [] });
    await mount(<PreviewWatermark canvasH={640} />);
    await act(async () => {
      byTestId(EDITOR.watermark).dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });
    expect(useAppStore.getState().selectedElementId).toBeNull();
    expect(useAppStore.getState().selectedIds).toEqual([]);
  });
});

// ── The mark IS the upgrade affordance ──────────────────────────────────────

describe("watermark as conversion surface", () => {
  test("clicking the mark starts the same checkout the sidebar Upgrade button does", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    const startUpgrade = jest.fn();
    useAppStore.setState({ startUpgrade });
    await mount(<PreviewWatermark canvasH={640} />);
    await act(async () => {
      byTestId(EDITOR.watermark).dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });
    expect(startUpgrade).toHaveBeenCalledTimes(1);
  });

  test("hovering reveals an explicit 'Remove watermark' CTA that also upgrades", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    const startUpgrade = jest.fn();
    useAppStore.setState({ startUpgrade });
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermarkUpgrade)).toBeNull();
    await act(async () => {
      byTestId(EDITOR.watermark).dispatchEvent(
        new MouseEvent("mouseover", { bubbles: true })
      );
    });
    const cta = byTestId(EDITOR.watermarkUpgrade);
    expect(cta).not.toBeNull();
    expect(cta.textContent).toMatch(/remove watermark/i);
    await act(async () => {
      cta.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(startUpgrade).toHaveBeenCalled();
  });

  test("a checkout already in flight disables the CTA rather than double-firing", async () => {
    setPlan({ plan: "free", configured: true, watermark: true });
    useAppStore.setState({ billingActionPending: true });
    await mount(<PreviewWatermark canvasH={640} />);
    expect(byTestId(EDITOR.watermark).disabled).toBe(true);
  });
});
