# selection_v2 open items

Logged for later; not actions now.

- **2026-10-25, speaker-fix confidence is not ground truth.** On Surya Ep 1 the Gemini speaker fix labels the
  delivery-sketch customer (L0421-L0487, a stranger per the spec) as "Siva" for 25 lines at confidence 0.9, and gives
  the stray 'హా' at 15:24.8 to Surya, who is not in that scene. Stage 1B must re-derive speakers per scene from the
  dialogue, using the fix only as a prior. Stage 1A surfaces this in `speaker_segments` (co-speakers per segment).
- **2026-10-25, ffmpeg scene detection missed a true hard cut.** `runs/video/p3XTZlZ6-xw/shots.json` (ffmpeg
  `select=gt(scene,0.25)` on 320 px frames) has cuts at 15:23.3 and 15:30.6 but none at the 15:24.8 scene change the
  spec singles out. Try a lower threshold and/or PySceneDetect on this episode and compare the detected cuts before
  trusting shot cuts as boundary evidence elsewhere.
