/**
 * Zoom² font-size regression (feature #3).
 *
 * Element bodies size text as fraction-of-canvas-height in LAYOUT px; the
 * stage then scales visually via the zoom wrapper's scale(zoom). Reading the
 * canvas height with getBoundingClientRect() returned the VISUAL height
 * (stage.h × zoom), so rendered text grew/shrank by zoom². ElementRenderer
 * must read the transform-immune LAYOUT height (offsetHeight = STAGE_DIMS.h)
 * so zoom scales every element exactly once, like the video itself.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useAppStore } from "@/store/useAppStore";
import { ElementRenderer } from "@/components/editor/ElementRenderer";

global.IS_REACT_ACT_ENVIRONMENT = true;

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

// A canvas node zoomed to 150%: layout height 640, visual height 960.
// jsdom never lays out, so both metrics are stubbed explicitly.
const makeFakeCanvas = ({ layoutH, visualH }) => {
  const node = document.createElement("div");
  Object.defineProperty(node, "offsetHeight", { value: layoutH });
  node.getBoundingClientRect = () => ({
    width: (visualH * 9) / 16,
    height: visualH,
    left: 0,
    top: 0,
    right: (visualH * 9) / 16,
    bottom: visualH,
  });
  return { current: node };
};

const HEADLINE = {
  id: "el-headline-test",
  type: "headline",
  visible: true,
  locked: false,
  x: 0.5,
  y: 0.12,
  rotation: 0,
  scale: 1,
  props: { text: "hook", font: "Inter", fontSize: 0.05, color: "#fff" },
};

afterEach(async () => {
  if (root) await unmount();
});

test("font size uses the LAYOUT canvas height, not the zoomed visual height", async () => {
  useAppStore.setState({ selectedElementId: null });
  const canvasRef = makeFakeCanvas({ layoutH: 640, visualH: 960 }); // zoom 1.5

  await mount(<ElementRenderer element={HEADLINE} canvasRef={canvasRef} />);

  const text = container.querySelector("[data-testid='canvas-el-el-headline-test'], [data-testid*='el-headline-test']");
  expect(text).not.toBeNull();
  const sized = text.querySelector("[style*='font-size']") || text;
  // 0.05 × 640 (layout) = 32px — NOT 0.05 × 960 (visual) = 48px, which the
  // zoom wrapper would then scale again to 72px (zoom² bug).
  expect(sized.style.fontSize).toBe("32px");
});

test("at zoom 1 both metrics agree — behavior unchanged", async () => {
  useAppStore.setState({ selectedElementId: null });
  const canvasRef = makeFakeCanvas({ layoutH: 640, visualH: 640 });

  await mount(<ElementRenderer element={HEADLINE} canvasRef={canvasRef} />);

  const text = container.querySelector("[data-testid*='el-headline-test']");
  const sized = text.querySelector("[style*='font-size']") || text;
  expect(sized.style.fontSize).toBe("32px");
});
