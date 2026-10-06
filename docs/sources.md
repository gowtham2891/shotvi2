# Data sources

Every video Shotvi was developed and tested on, with its YouTube link and the Scribe transcript in this repo.
Transcripts come from ElevenLabs Scribe v2 (`transcribe_scribe.py`; long files via `tools/transcribe_split.py`;
creator Shorts via `tools/transcribe_shorts.py`). Videos and audio are not in the repo; re-download them with
`python download_youtube.py <url> scribe_dataset`. Episode metadata is in `scribe_dataset/<id>/youtube_meta.json`.

Transcripts are machine-generated (they contain recognition errors) and the videos belong to their creators;
they are here for research on clip selection.

## Episodes

| Video | Channel | Length | Id | Transcript |
|---|---|---|---|---|
| [Jabardasth | 20th September 2026 | Full Episode | Rashmi, Nagababu, Indraja | ETV Telugu](https://www.youtube.com/watch?v=2sqpFVPc85o) | ETV Jabardasth | 63:55 | `2sqpFVPc85o` | [`2sqpFVPc85o_scribe_transcript.json`](../scribe_dataset/2sqpFVPc85o/2sqpFVPc85o_scribe_transcript.json) |
| [Hello Madam | Episode - 6 | Sheetal Gauthaman | Mohit Pedada | Vivan Suryaa | Infinitum Media](https://www.youtube.com/watch?v=epZ0lbXkgWA) | Sheetal Gauthaman | 26:49 | `epZ0lbXkgWA` | [`epZ0lbXkgWA_scribe_transcript.json`](../scribe_dataset/epZ0lbXkgWA/epZ0lbXkgWA_scribe_transcript.json) |
| [🔥🤩 HIS VIBE IN THIS EPISODE 👌🏻 | FT ARJUN SARJA GARU | Telugu Podcast | Raw Talks With VK | EP - 118](https://www.youtube.com/watch?v=FS1DI5D_hUk) | Raw Talks With VK | 54:30 | `FS1DI5D_hUk` | [`FS1DI5D_hUk_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/FS1DI5D_hUk_scribe_transcript.json) |
| [Couple Goals || Episode - 1 || Sushma Gopal || Mohit Pedada || Telugu Web Series || Infinitum Media](https://www.youtube.com/watch?v=j14Om1SaFYE) | Sushma Gopal | 19:27 | `j14Om1SaFYE` | [`j14Om1SaFYE_scribe_transcript.json`](../scribe_dataset/j14Om1SaFYE/j14Om1SaFYE_scribe_transcript.json) |
| [😜🔥SISINDRI ON RAW TALKSSSS | Akhil Akkineni Opens Up About Failure, Comebacks & Family Life |](https://www.youtube.com/watch?v=LgjEP0dsQZc) | Raw Talks With VK | 111:57 | `LgjEP0dsQZc` | [`LgjEP0dsQZc_scribe_transcript.json`](../scribe_dataset/LgjEP0dsQZc/LgjEP0dsQZc_scribe_transcript.json) |
| [🔥 ₹23,000 Crore Pharma Empire: Money, Business & The Untold Story of Granules Ft. Uma Chigurupati](https://www.youtube.com/watch?v=mdxzKJ2gTQ4) | Telugu Connects - Growth Podcast  | 95:47 | `mdxzKJ2gTQ4` | [`mdxzKJ2gTQ4_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/mdxzKJ2gTQ4_scribe_transcript.json) |
| [The Ultimate Hair Care Guide 💇‍♂️ | Hair Fall, Growth, Dandruff & More | with Dr. John Watts](https://www.youtube.com/watch?v=N6wJ-qR8uxY) | PrasadLifeStyle | 117:14 | `N6wJ-qR8uxY` | [`N6wJ-qR8uxY_scribe_transcript.json`](../scribe_dataset/N6wJ-qR8uxY/N6wJ-qR8uxY_scribe_transcript.json) |
| [Couple Goals || Episode - 2 || Sushma Gopal || Mohit Pedada || Telugu Web Series || Infinitum Media](https://www.youtube.com/watch?v=QWIvgoTQGKk) | Sushma Gopal | 21:52 | `QWIvgoTQGKk` | [`QWIvgoTQGKk_scribe_transcript.json`](../scribe_dataset/QWIvgoTQGKk/QWIvgoTQGKk_scribe_transcript.json) |
| [Paradise Fitness Coach | Stop Doing These Health Mistakes ! Vodcast with Prasad Ep 5 #paradise](https://www.youtube.com/watch?v=TBlm6wcij94) | PrasadLifeStyle | 134:34 | `TBlm6wcij94` | [`TBlm6wcij94_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/TBlm6wcij94_scribe_transcript.json) |

## Creator Shorts (used as recall anchors for podcasts)

| Short | From episode | Transcript |
|---|---|---|
| [`7spOv2MtDnc`](https://www.youtube.com/shorts/7spOv2MtDnc) | `FS1DI5D_hUk` | [`7spOv2MtDnc_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/7spOv2MtDnc_scribe_transcript.json) |
| [`dFFP12qq6FI`](https://www.youtube.com/shorts/dFFP12qq6FI) | `FS1DI5D_hUk` | [`dFFP12qq6FI_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/dFFP12qq6FI_scribe_transcript.json) |
| [`GRCxRRaFOcI`](https://www.youtube.com/shorts/GRCxRRaFOcI) | `FS1DI5D_hUk` | [`GRCxRRaFOcI_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/GRCxRRaFOcI_scribe_transcript.json) |
| [`jnNwSIge0LU`](https://www.youtube.com/shorts/jnNwSIge0LU) | `FS1DI5D_hUk` | [`jnNwSIge0LU_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/jnNwSIge0LU_scribe_transcript.json) |
| [`JsDeHLGLlks`](https://www.youtube.com/shorts/JsDeHLGLlks) | `FS1DI5D_hUk` | [`JsDeHLGLlks_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/JsDeHLGLlks_scribe_transcript.json) |
| [`QWcRd6IYwWU`](https://www.youtube.com/shorts/QWcRd6IYwWU) | `FS1DI5D_hUk` | [`QWcRd6IYwWU_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/QWcRd6IYwWU_scribe_transcript.json) |
| [`t02TgY0Ggqc`](https://www.youtube.com/shorts/t02TgY0Ggqc) | `FS1DI5D_hUk` | [`t02TgY0Ggqc_scribe_transcript.json`](../scribe_dataset/FS1DI5D_hUk/creator_shorts/t02TgY0Ggqc_scribe_transcript.json) |
| [`4USAUQTVQxg`](https://www.youtube.com/shorts/4USAUQTVQxg) | `mdxzKJ2gTQ4` | [`4USAUQTVQxg_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/4USAUQTVQxg_scribe_transcript.json) |
| [`5HFRXNylY1A`](https://www.youtube.com/shorts/5HFRXNylY1A) | `mdxzKJ2gTQ4` | [`5HFRXNylY1A_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/5HFRXNylY1A_scribe_transcript.json) |
| [`8wM_AoShEXI`](https://www.youtube.com/shorts/8wM_AoShEXI) | `mdxzKJ2gTQ4` | [`8wM_AoShEXI_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/8wM_AoShEXI_scribe_transcript.json) |
| [`_gfj-FhsOB8`](https://www.youtube.com/shorts/_gfj-FhsOB8) | `mdxzKJ2gTQ4` | [`_gfj-FhsOB8_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/_gfj-FhsOB8_scribe_transcript.json) |
| [`E2TOXSCqVdQ`](https://www.youtube.com/shorts/E2TOXSCqVdQ) | `mdxzKJ2gTQ4` | [`E2TOXSCqVdQ_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/E2TOXSCqVdQ_scribe_transcript.json) |
| [`FE-14LkvJNI`](https://www.youtube.com/shorts/FE-14LkvJNI) | `mdxzKJ2gTQ4` | [`FE-14LkvJNI_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/FE-14LkvJNI_scribe_transcript.json) |
| [`JpAq-bk5RgA`](https://www.youtube.com/shorts/JpAq-bk5RgA) | `mdxzKJ2gTQ4` | [`JpAq-bk5RgA_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/JpAq-bk5RgA_scribe_transcript.json) |
| [`ox-jOxTbw-c`](https://www.youtube.com/shorts/ox-jOxTbw-c) | `mdxzKJ2gTQ4` | [`ox-jOxTbw-c_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/ox-jOxTbw-c_scribe_transcript.json) |
| [`pjQu-ctYnEM`](https://www.youtube.com/shorts/pjQu-ctYnEM) | `mdxzKJ2gTQ4` | [`pjQu-ctYnEM_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/pjQu-ctYnEM_scribe_transcript.json) |
| [`Qb4_-B-Re74`](https://www.youtube.com/shorts/Qb4_-B-Re74) | `mdxzKJ2gTQ4` | [`Qb4_-B-Re74_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/Qb4_-B-Re74_scribe_transcript.json) |
| [`qgfnG5EUsuI`](https://www.youtube.com/shorts/qgfnG5EUsuI) | `mdxzKJ2gTQ4` | [`qgfnG5EUsuI_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/qgfnG5EUsuI_scribe_transcript.json) |
| [`U39ZhvBd550`](https://www.youtube.com/shorts/U39ZhvBd550) | `mdxzKJ2gTQ4` | [`U39ZhvBd550_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/U39ZhvBd550_scribe_transcript.json) |
| [`VApuq93yxeA`](https://www.youtube.com/shorts/VApuq93yxeA) | `mdxzKJ2gTQ4` | [`VApuq93yxeA_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/VApuq93yxeA_scribe_transcript.json) |
| [`yZ9ulvTAtWY`](https://www.youtube.com/shorts/yZ9ulvTAtWY) | `mdxzKJ2gTQ4` | [`yZ9ulvTAtWY_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/yZ9ulvTAtWY_scribe_transcript.json) |
| [`Z6gDXXoC-MY`](https://www.youtube.com/shorts/Z6gDXXoC-MY) | `mdxzKJ2gTQ4` | [`Z6gDXXoC-MY_scribe_transcript.json`](../scribe_dataset/mdxzKJ2gTQ4/creator_shorts/Z6gDXXoC-MY_scribe_transcript.json) |
| [`F6paX54K3EA`](https://www.youtube.com/shorts/F6paX54K3EA) | `TBlm6wcij94` | [`F6paX54K3EA_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/F6paX54K3EA_scribe_transcript.json) |
| [`FDApN1_cA7U`](https://www.youtube.com/shorts/FDApN1_cA7U) | `TBlm6wcij94` | [`FDApN1_cA7U_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/FDApN1_cA7U_scribe_transcript.json) |
| [`GYzlHZc96fU`](https://www.youtube.com/shorts/GYzlHZc96fU) | `TBlm6wcij94` | [`GYzlHZc96fU_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/GYzlHZc96fU_scribe_transcript.json) |
| [`lAuFqImfnTE`](https://www.youtube.com/shorts/lAuFqImfnTE) | `TBlm6wcij94` | [`lAuFqImfnTE_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/lAuFqImfnTE_scribe_transcript.json) |
| [`M-kH9Et4dYQ`](https://www.youtube.com/shorts/M-kH9Et4dYQ) | `TBlm6wcij94` | [`M-kH9Et4dYQ_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/M-kH9Et4dYQ_scribe_transcript.json) |
| [`NfkuEL-DfM4`](https://www.youtube.com/shorts/NfkuEL-DfM4) | `TBlm6wcij94` | [`NfkuEL-DfM4_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/NfkuEL-DfM4_scribe_transcript.json) |
| [`nkcaeqAUS8E`](https://www.youtube.com/shorts/nkcaeqAUS8E) | `TBlm6wcij94` | [`nkcaeqAUS8E_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/nkcaeqAUS8E_scribe_transcript.json) |
| [`qMt-0oWPHOo`](https://www.youtube.com/shorts/qMt-0oWPHOo) | `TBlm6wcij94` | [`qMt-0oWPHOo_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/qMt-0oWPHOo_scribe_transcript.json) |
| [`t3h27qzI4qg`](https://www.youtube.com/shorts/t3h27qzI4qg) | `TBlm6wcij94` | [`t3h27qzI4qg_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/t3h27qzI4qg_scribe_transcript.json) |
| [`t9LLIWCJVd8`](https://www.youtube.com/shorts/t9LLIWCJVd8) | `TBlm6wcij94` | [`t9LLIWCJVd8_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/t9LLIWCJVd8_scribe_transcript.json) |
| [`taht6q0xIRg`](https://www.youtube.com/shorts/taht6q0xIRg) | `TBlm6wcij94` | [`taht6q0xIRg_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/taht6q0xIRg_scribe_transcript.json) |
| [`zwAKRLCoe-Y`](https://www.youtube.com/shorts/zwAKRLCoe-Y) | `TBlm6wcij94` | [`zwAKRLCoe-Y_scribe_transcript.json`](../scribe_dataset/TBlm6wcij94/creator_shorts/zwAKRLCoe-Y_scribe_transcript.json) |

## Ground truth

- [`epZ0lbXkgWA.json`](../data/gold/epZ0lbXkgWA.json)
- [`FS1DI5D_hUk.json`](../data/gold/FS1DI5D_hUk.json)
- [`j14Om1SaFYE.json`](../data/gold/j14Om1SaFYE.json)
- [`QWIvgoTQGKk.json`](../data/gold/QWIvgoTQGKk.json)
- [`TBlm6wcij94.json`](../data/gold/TBlm6wcij94.json)

Web-series ground truth (Couple Goals Ep1/Ep2, Hello Madam Ep6) was picked by the `clip-gt-editor` agent and reviewed by the founder.
