"""Shared pytest fixtures and Hypothesis profiles.

Profiles (selected with ``HYPOTHESIS_PROFILE``, default ``dev``):

- ``dev``: 50 examples, fast enough for the edit loop.
- ``ci``: 300 examples, no deadline (CI hardware is noisy).
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("dev", max_examples=50)
settings.register_profile("ci", max_examples=300, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
