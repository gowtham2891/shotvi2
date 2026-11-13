// Decodes ASS/SSA subtitle color hex (&HAABBGGRR) into CSS-usable values.
//
// ASS alpha convention (confirmed against services/caption_renderer.py's own
// typewriter comment: color_unspoken "&HFFFFFFFF" = "future words fully
// transparent (not yet typed)" — alpha byte 0xFF must mean invisible):
//   alpha byte 0x00 -> fully OPAQUE   (css opacity 1)
//   alpha byte 0xFF -> fully TRANSPARENT (css opacity 0)
//   css_opacity = 1 - (alpha_byte / 255)
//
// This is the reverse of what most developers expect from a "AARRGGBB"-style
// alpha channel, which is exactly why it's worth a decoder + tests rather
// than hand-computed hex.
export function decodeAssColor(assHex) {
  const clean = String(assHex).replace(/^&H/i, "").padStart(8, "0");
  const aa = parseInt(clean.slice(0, 2), 16);
  const bb = parseInt(clean.slice(2, 4), 16);
  const gg = parseInt(clean.slice(4, 6), 16);
  const rr = parseInt(clean.slice(6, 8), 16);
  const toHex2 = (n) => n.toString(16).padStart(2, "0");
  return {
    hex: `#${toHex2(rr)}${toHex2(gg)}${toHex2(bb)}`,
    opacity: Math.round((1 - aa / 255) * 1000) / 1000,
    r: rr,
    g: gg,
    b: bb,
    alphaByte: aa,
  };
}
