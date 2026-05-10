"""KTO preference-optimization training (PLANNED — not yet implemented).

Plan
----
After the SFT track stabilizes, train Kahneman-Tversky Optimization on top of
the latest SFT checkpoint using binary desirable/undesirable labels derived
from scored Claude trajectories.

High-level shape:
    - Score sessions 0-1 from outcome signals (quest progress, deaths, churn)
      via `score_sessions.py`; top fraction → desirable, bottom → undesirable.
    - Build sliding-window KTO records keyed off the scored sessions.
    - Train on Modal H100 from the merged SFT weights as the policy init,
      reusing the shared chat-template patch + render path in `finetune/render.py`.
    - Deploy alongside the SFT serve for A/B on the live Core 3 benchmark.

See `research/decisions/why-kto-over-ppo.md` and the r6-KTO smoke notes in
`research/experiments/training-runs.md` for context.
"""
import sys

if __name__ == "__main__":
    sys.exit("train_kto_modal.py is a planning stub — see module docstring.")
