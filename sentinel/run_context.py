# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Run-scoped paths shared across Sentinel phase modules.

A ``RunContext`` is built by :func:`sentinel.main.execute_phases` once
per config and passed explicitly to every phase. It replaces the legacy
``_run_dir`` / ``_run_dir_path`` / ``cloned_repo_path`` keys that used
to ride on the config dict as an implicit side-channel.

This module is part of the public package surface: ``RunContext`` is
re-exported from :mod:`sentinel` so tests and callers have a stable
entry point for constructing run-scoped path state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RunContext:
    """Per-run paths visible to every phase.

    Attributes:
        run_dir: The unique ``<project>/<timestamp>/`` folder for this run.
        repos_root: Directory where ``fetch_code`` stages source trees,
            i.e. ``<run_dir>/repos``.
        repo_path: Absolute path to the fetched code root. ``None`` until
            ``fetch_code`` populates it; downstream phases that read it
            should do so via :pyattr:`repo_root` to keep the pre-fetch
            fallback consistent.
        fetch_attempted: Set to ``True`` by ``fetch_code`` before any IO,
            so downstream phases can distinguish "fetch ran and failed
            (leaving partial residue under repos_root)" from "fetch
            was skipped and the caller pre-staged sources directly".
            Both leave ``repo_path is None`` but only the former should
            short-circuit with a targeted "no repository available"
            diagnostic.
    """

    run_dir: Path
    repos_root: Path
    repo_path: Optional[Path] = None
    fetch_attempted: bool = False

    @classmethod
    def for_run(cls, run_dir: Path) -> "RunContext":
        """Build a context anchored at ``run_dir`` with the standard layout."""
        return cls(run_dir=Path(run_dir), repos_root=Path(run_dir) / "repos")

    @property
    def repo_root(self) -> Path:
        """Source tree to operate on.

        Returns ``repo_path`` once ``fetch_code`` has set it, otherwise the
        ``repos_root`` placeholder. Phases that need the *fetched* tree
        specifically should check ``repo_path`` directly.
        """
        return self.repo_path if self.repo_path is not None else self.repos_root

    def output_dir(self, name: str) -> Path:
        """Return ``<run_dir>/<name>`` (no side effects)."""
        return self.run_dir / name
