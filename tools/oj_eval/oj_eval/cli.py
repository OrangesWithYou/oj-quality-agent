from __future__ import annotations

"""Installed command entry point.

This module keeps the packaged command stable while delegating all behavior to
the existing application CLI.
"""

from pipeline_app.cli import build_arg_parser, main, run_pipeline

__all__ = ["build_arg_parser", "main", "run_pipeline"]
