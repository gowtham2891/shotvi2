# Known issues

## Backend: `red-pop` / `clean-dark` caption box alpha looks off

**Where:** `services/caption_renderer.py` (in the `clipforge-ai` backend repo), `STYLES["red-pop"]["back_color"]` and `STYLES["clean-dark"]["back_color"]`.

**Found while:** building `frontend/src/data/captionStylePreview.js`, mapping each backend caption style to a canvas-preview CSS approximation (2026-07).

**What was found:** ASS colors use the `&HAABBGGRR` format, where the alpha byte follows true ASS semantics: `0x00` = fully opaque, `0xFF` = fully transparent. This is confirmed unambiguously by `typewriter`'s `color_unspoken: "&HFFFFFFFF"`, whose own inline comment says "future words fully transparent (not yet typed)" — i.e. alpha `0xFF` → invisible.

Applying that same, now-confirmed formula to the two styles in question:

| Style | `back_color` hex | Alpha byte | Decoded opacity | Inline comment claims |
|---|---|---|---|---|
| `red-pop` | `&HCC000000` | `0xCC` (204) | `1 - 204/255 ≈ 0.20` | "Near-opaque black box" |
| `clean-dark` | `&HDD000000` | `0xDD` (221) | `1 - 221/255 ≈ 0.13` | "Dark solid bar" |

Both styles are `border_style: 3` (opaque box), so this alpha value genuinely controls a visible background box behind the caption line — it isn't inert. The literal hex, read via the same formula that correctly resolves every other style in the file (including the unambiguous `typewriter` case above), decodes to a fairly *transparent* box (~13–20% opacity) for both — not the "near-opaque" / "solid" look the comments describe.

**What we did about it:** the frontend preview (`captionStylePreview.js`) mirrors the literal hex value (~0.2 / ~0.13 opacity), not the comment's claimed intent, since the goal was "preview ≈ what the backend actually burns in." If the backend hex is in fact wrong, the preview is faithfully wrong the same way — better than silently drifting from the real export.

**Re-verified (2026-07):** a real visual bug report (captions rendering as dark, barely-visible blobs) raised the alternative hypothesis that the alpha convention was inverted somewhere in the frontend decode, which — if true — would mean these two boxes are actually ~80–87% opaque and the source comments are correct after all. Re-checked three independent ways: hand arithmetic, a standalone Python cross-check, and a proper decoder (`src/lib/assColor.js`) with unit tests (`src/__tests__/captionStylePreview.test.js`) asserting decoded colors against every style's human-readable name. All three agreed with the original finding above. **This conclusion has since been superseded — see the entry directly below.**

### ⚠ SUPERSEDED (2026-07) — the real renderer ignores `BackColour` alpha entirely for `border_style: 3` boxes

During Phase 1 exploration of the backend overlay-burn-in project, the alpha question was settled empirically instead of by further hex-decode argument: a test `.ass` file with a deliberately-chosen alpha byte of `0x80` (the exact 50% midpoint — identical under either byte-order convention) was burned onto a white test video with the actual `ffmpeg`/libass build `worker.py` invokes (FFmpeg 8.0.1, libass via `--enable-libass`). The box rendered as **pure solid `(0,0,0)` black**, not a ~50%-blended grey.

**Verdict: this libass build does not honor `BackColour`'s alpha channel for `BorderStyle=3` box fills at all.** Every box — `red-pop`, `clean-dark`, `hormozi`, `neon-green`, `typewriter` — renders **fully opaque** in the real export, regardless of what its alpha byte decodes to under any convention. The two analyses above (ours and the "re-verified" pass) were both correct about the *arithmetic* and both irrelevant to the *actual rendered output*, since the alpha channel is simply inert in practice.

**Action for the frontend (not fixed yet — logging so it isn't lost):**
- `captionStylePreview.js`'s `boxStyle(opacity)` calls for `red-pop` (0.2), `clean-dark` (0.13), `hormozi` (0.92), `neon-green` (0.88), and `typewriter` (0.7) should all be changed to **opacity 1.0** — every `border_style: 3` style's preview box should render fully solid, not partially transparent, to match the real export.
- Update the corresponding assertions in `captionStylePreview.test.js`.

## Font dropdown: 4 of 6 fonts aren't actually loaded in the browser preview

**Where:** `Inspector.jsx`'s headline font `<select>` (values from `FONTS` in `mockData.js`: `Outfit, Manrope, Bebas Neue, Anton, Poppins, Playfair Display`), vs. the actual `@import` in `index.css` and the `<link>` in `public/index.html`.

**Found while:** Phase 1 exploration of the backend overlay-burn-in project (checking font parity between preview and export).

**What was found:** only **Outfit**, **Manrope**, and **JetBrains Mono** are actually pulled in via Google Fonts (`index.css`'s `@import url(...)`). **Bebas Neue, Anton, Poppins, and Playfair Display are offered in the headline font dropdown but never loaded anywhere** — selecting one of these silently falls back to the browser's default sans-serif, with no indication to the user that their choice didn't apply.

**Action (not fixed yet — logging so it isn't lost):** either add the missing four fonts to the `index.css` `@import` (or a dedicated `<link>`), or trim the `FONTS` list down to the fonts that are actually loaded, so the dropdown never offers a choice that silently does nothing.
