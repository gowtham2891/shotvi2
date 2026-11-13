/**
 * Feature #13 — punch-point store slice + export wire.
 * punchPoints: null = auto (from word beats); an array (incl. []) = user's
 * final say. Effective set → crop_keyframes only at the wire boundary.
 */
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest } from "@/api/renders";
import { punchesToKeyframes } from "@/lib/autoZoom";

const st = () => useAppStore.getState();

const WORDS = [
  { start: 0.0, end: 0.4 },
  { start: 0.9, end: 1.3 }, // beat
  { start: 1.3, end: 1.7 },
  { start: 5.0, end: 5.4 }, // beat
];

beforeEach(() => {
  useAppStore.setState({
    transcript: WORDS,
    duration: 6.0,
    exportSettings: { ...useAppStore.getState().exportSettings, punchPoints: null },
  });
  st().resetHistory();
});

describe("effective punch points", () => {
  test("null → auto set from word beats", () => {
    expect(st().getEffectivePunchPoints()).toEqual([0.9, 5.0]);
    expect(st().exportSettings.punchPoints).toBeNull();
  });

  test("togglePunchPoint materializes auto set + the toggle", () => {
    st().togglePunchPoint(3.0); // add a new one
    expect(st().exportSettings.punchPoints).toEqual([0.9, 3.0, 5.0]);
  });

  test("togglePunchPoint near an existing point removes it", () => {
    st().togglePunchPoint(0.95); // within tol of the 0.9 auto beat
    expect(st().exportSettings.punchPoints).toEqual([5.0]);
  });

  test("autoGeneratePunchPoints re-seeds explicitly", () => {
    useAppStore.setState({
      exportSettings: { ...st().exportSettings, punchPoints: [] },
    });
    st().autoGeneratePunchPoints();
    expect(st().exportSettings.punchPoints).toEqual([0.9, 5.0]);
  });

  test("clearPunchPoints materializes [] (distinct from null/auto)", () => {
    st().clearPunchPoints();
    expect(st().exportSettings.punchPoints).toEqual([]);
    expect(st().getEffectivePunchPoints()).toEqual([]);
  });

  test("a punch toggle is one undo frame", () => {
    st().togglePunchPoint(3.0);
    expect(st().exportSettings.punchPoints).toEqual([0.9, 3.0, 5.0]);
    st().undo();
    expect(st().exportSettings.punchPoints).toBeNull();
  });
});

describe("export wire", () => {
  test("non-empty keyframes cross as crop_keyframes", () => {
    const kfs = punchesToKeyframes([0.9, 5.0], 6.0);
    const req = buildRerenderRequest({ cropKeyframes: kfs });
    expect(req.crop_keyframes).toEqual(kfs);
    expect(req.crop_keyframes.length).toBeGreaterThan(0);
  });

  test("empty / null keyframes omit the field (byte-identical payload)", () => {
    expect("crop_keyframes" in buildRerenderRequest({ cropKeyframes: [] })).toBe(false);
    expect("crop_keyframes" in buildRerenderRequest({ cropKeyframes: null })).toBe(false);
    expect("crop_keyframes" in buildRerenderRequest({})).toBe(false);
  });
});
