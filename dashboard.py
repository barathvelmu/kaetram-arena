#!/usr/bin/env python3
"""Live dashboard for Kaetram AI Agent — serves on port 8080.

Supports both single-agent (play.sh) and multi-agent (orchestrate.py) modes.

This is a thin launcher; all logic lives in the dashboard/ package.
"""

from dashboard import start_dashboard

if __name__ == "__main__":
    start_dashboard()
