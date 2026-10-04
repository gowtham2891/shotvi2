"""Segment tests run offline: the opening detector uses the rules path unless a test injects a fake LLM."""
import os

os.environ["SHOTVI_OPENING_MODE"] = "rules"
