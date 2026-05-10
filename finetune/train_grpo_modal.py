"""GRPO (Group Relative Policy Optimization) training (PLANNED — not yet implemented).

Plan
----
Once SFT (and optionally KTO) is in place, run GRPO from the latest merged
SFT checkpoint as the policy init. The model generates rollouts and is
scored on game-derived rewards — learning *what works*, not just *what was
demonstrated*.

High-level shape:
    - Build a prompt-only dataset from extracted Claude observations.
    - Reward signal from gameplay outcomes (quest stage advances, kills,
      deaths, BFS/warp adherence).
    - Train on Modal H100 reusing the shared chat-template patch + render
      path in `finetune/render.py`.
    - Deploy alongside SFT/KTO for live Core 3 A/B.

See `research/experiments/training-runs.md` for the surrounding ladder.
"""
import sys

if __name__ == "__main__":
    sys.exit("train_grpo_modal.py is a planning stub — see module docstring.")
