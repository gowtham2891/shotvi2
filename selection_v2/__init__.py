"""selection_v2: the six-stage clip-selection pipeline (docs-1/klipvi_Clip_Selection_Pipeline.docx).

Additive to the existing ``shotvi.select`` pipeline, which is untouched. One module per stage; every stage reads
and writes ``runs/selection_v2/<video_id>/episode_facts.json`` (validated against ``schemas/``); stages never call
each other, ``runner.run_stage`` does. Debug UI: ``streamlit run selection_v2/debug_app.py``.
"""
