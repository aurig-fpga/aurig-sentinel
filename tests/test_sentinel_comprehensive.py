# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Comprehensive test suite for Sentinel (YAML schema v1.0).

Covers config loading & validation, config discovery (top-level only,
priority order, disabled/ convention), multi-config processing with
skip-and-continue, fetch_code, synthesis (vivado backend including
auto-discovered and explicit synthesis script paths, timeout handling,
and error contracts for unsupported tools / missing tool paths), and a
couple of error-handling cases.
"""

import io
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_YAML_TEMPLATE = textwrap.dedent("""\
    schema_version: "1.0"
    project:
      name: {name}
    fetch:
      type: git
      url: https://github.com/example/{name}.git
      branch: main
    project_manifest: manifest.txt
    phases:
      synthesis:
        enabled: true
""")


def write_yaml(path: Path, name: str = "demo") -> Path:
    path.write_text(VALID_YAML_TEMPLATE.format(name=name), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Configuration loading (YAML, schema v1.0)
# ---------------------------------------------------------------------------

class TestConfigurationLoading(unittest.TestCase):
    """Loading and validating YAML config files."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_valid_config(self):
        from sentinel.config_validator import load_and_validate

        path = write_yaml(self.test_dir / "valid.yaml", name="alpha")
        loaded = load_and_validate(str(path))

        self.assertEqual(loaded["schema_version"], "1.0")
        self.assertEqual(loaded["project"]["name"], "alpha")
        self.assertEqual(loaded["fetch"]["type"], "git")
        self.assertTrue(loaded["phases"]["synthesis"]["enabled"])

    def test_load_missing_config_file(self):
        from sentinel.config_validator import ConfigValidationError, load_config_file

        with self.assertRaises(ConfigValidationError) as ctx:
            load_config_file(str(self.test_dir / "does-not-exist.yaml"))
        self.assertTrue(ctx.exception.is_hard)
        self.assertIn("not found", str(ctx.exception))

    def test_load_unparsable_yaml(self):
        from sentinel.config_validator import ConfigValidationError, load_config_file

        bad = self.test_dir / "broken.yaml"
        bad.write_text("schema_version: \"1.0\"\nproject:\n  name: x\n  : invalid", encoding="utf-8")

        with self.assertRaises(ConfigValidationError) as ctx:
            load_config_file(str(bad))
        self.assertTrue(ctx.exception.is_hard)
        self.assertIn("YAML parse error", str(ctx.exception))

    def test_unsupported_schema_version_rejected(self):
        from sentinel.config_validator import ConfigValidationError, load_and_validate

        path = self.test_dir / "v2.yaml"
        path.write_text(VALID_YAML_TEMPLATE.format(name="x").replace('"1.0"', '"2.0"'), encoding="utf-8")

        with self.assertRaises(ConfigValidationError) as ctx:
            load_and_validate(str(path))
        self.assertFalse(ctx.exception.is_hard)
        self.assertTrue(any("schema_version" in e and "2.0" in e for e in ctx.exception.errors))

    def test_no_phase_enabled_rejected(self):
        from sentinel.config_validator import ConfigValidationError, load_and_validate

        path = self.test_dir / "noop.yaml"
        path.write_text(VALID_YAML_TEMPLATE.format(name="noop").replace("enabled: true", "enabled: false"), encoding="utf-8")

        with self.assertRaises(ConfigValidationError) as ctx:
            load_and_validate(str(path))
        self.assertTrue(any("at least one phase must be enabled" in e for e in ctx.exception.errors))

    def test_validate_collects_multiple_errors(self):
        from sentinel.config_validator import ConfigValidationError, validate_config

        bad = {
            "schema_version": "1.0",
            "project": {},                         # missing name
            "fetch": {"type": "git"},              # missing url
            "phases": {"synthesis": {"enabled": False}},  # nothing enabled, also project_manifest missing
        }
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(bad)
        self.assertGreaterEqual(len(ctx.exception.errors), 3)
        self.assertFalse(ctx.exception.is_hard)

    def test_yaml_parse_error_does_not_leak_path(self):
        """PyYAML's MarkedYAMLError embeds the stream name in str(exc).

        When a file object is passed to ``yaml.safe_load``, PyYAML
        reads the stream's ``.name`` attribute to populate the mark —
        which on a real file is the path. ``load_config_file`` reads
        the text first and then parses the string, so the mark name
        defaults to ``<unicode string>`` and the path never enters
        the message.
        """
        from sentinel.config_validator import ConfigValidationError, load_config_file

        bad = self.test_dir / "tricky-name-broken.yaml"
        bad.write_text("foo: [unbalanced\n", encoding="utf-8")

        with self.assertRaises(ConfigValidationError) as ctx:
            load_config_file(str(bad))

        msg = ctx.exception.errors[0]
        self.assertIn("YAML parse error", msg)
        # PyYAML's mark must say <unicode string>, never the path.
        self.assertIn("<unicode string>", msg)
        self.assertNotIn(str(bad), msg)
        self.assertNotIn("tricky-name-broken.yaml", msg)
        self.assertTrue(ctx.exception.is_hard)

    def test_oserror_during_load_does_not_leak_path(self):
        """A PermissionError (or any OSError) on open() must produce
        a path-free reason in the ConfigValidationError message.

        The runner's caller-side code prefixes the path exactly once
        (in `_process_config`'s log line and in the collision "note:"
        block). Letting `str(exc)` reach the message would render as
        `[Errno N] strerror: '<path>'` and reintroduce the path
        duplication that was just removed at source.
        """
        from unittest.mock import patch
        from sentinel.config_validator import ConfigValidationError, load_config_file

        leaky = PermissionError(13, "Permission denied", "/some/path/cfg.yaml")
        with patch("pathlib.Path.open", side_effect=leaky):
            with self.assertRaises(ConfigValidationError) as ctx:
                load_config_file("/some/path/cfg.yaml")

        msgs = ctx.exception.errors
        self.assertEqual(len(msgs), 1)
        # Reason carries the human-readable cause...
        self.assertIn("Permission denied", msgs[0])
        # ...but NOT the filename that str(exc) would have leaked.
        self.assertNotIn("/some/path/cfg.yaml", msgs[0])
        self.assertNotIn("'cfg.yaml'", msgs[0])
        self.assertTrue(ctx.exception.is_hard)

    def test_validator_accepts_nonexistent_local_path(self):
        """fetch.local_path is *not* checked against the host filesystem.

        The validator only enforces schema/format. Existence is verified
        at runtime by fetch_code so that --dry-run on a developer
        laptop, in CI, or under a Copilot lint pass does not reject a
        config that targets a path only present on the production host.
        """
        from sentinel.config_validator import validate_config

        ghost = self.test_dir / "definitely-not-here"
        config = {
            "schema_version": "1.0",
            "project": {"name": "alpha"},
            "fetch": {"type": "local", "local_path": str(ghost)},
            "project_manifest": "manifest.txt",
            "phases": {"synthesis": {"enabled": True}},
        }
        # No exception → contract satisfied.
        validate_config(config)
        self.assertFalse(ghost.exists(), "ghost path must not actually exist for this test")


# ---------------------------------------------------------------------------
# Config discovery (top-level YAMLs only)
# ---------------------------------------------------------------------------

class TestDiscoverConfigs(unittest.TestCase):
    """discover_configs() filters and ordering."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_returns_top_level_yaml_sorted(self):
        from sentinel.config_validator import discover_configs

        for name in ["beta.yaml", "alpha.yaml", "gamma.yaml"]:
            (self.test_dir / name).write_text("schema_version: \"1.0\"\n", encoding="utf-8")

        result = discover_configs(str(self.test_dir))
        self.assertEqual(
            [Path(p).name for p in result],
            ["alpha.yaml", "beta.yaml", "gamma.yaml"],
        )

    def test_excludes_non_yaml_extensions(self):
        from sentinel.config_validator import discover_configs

        (self.test_dir / "valid.yaml").write_text("---\n", encoding="utf-8")
        (self.test_dir / "legacy.json").write_text("{}", encoding="utf-8")
        (self.test_dir / "shortform.yml").write_text("---\n", encoding="utf-8")
        (self.test_dir / "swap.yaml.swp").write_text("---\n", encoding="utf-8")

        result = [Path(p).name for p in discover_configs(str(self.test_dir))]
        self.assertEqual(result, ["valid.yaml"])

    def test_excludes_hidden_files(self):
        from sentinel.config_validator import discover_configs

        (self.test_dir / "visible.yaml").write_text("---\n", encoding="utf-8")
        (self.test_dir / ".hidden.yaml").write_text("---\n", encoding="utf-8")

        result = [Path(p).name for p in discover_configs(str(self.test_dir))]
        self.assertEqual(result, ["visible.yaml"])

    def test_excludes_subdirectory_contents_including_disabled(self):
        from sentinel.config_validator import discover_configs

        (self.test_dir / "active.yaml").write_text("---\n", encoding="utf-8")

        disabled = self.test_dir / "disabled"
        disabled.mkdir()
        (disabled / "parked.yaml").write_text("---\n", encoding="utf-8")

        nested = self.test_dir / "nested"
        nested.mkdir()
        (nested / "inner.yaml").write_text("---\n", encoding="utf-8")

        result = [Path(p).name for p in discover_configs(str(self.test_dir))]
        self.assertEqual(result, ["active.yaml"])

    def test_missing_directory_returns_empty_list(self):
        from sentinel.config_validator import discover_configs
        self.assertEqual(discover_configs(str(self.test_dir / "nope")), [])

    def test_does_not_validate(self):
        """discover_configs returns malformed yamls just as well as good ones."""
        from sentinel.config_validator import discover_configs

        (self.test_dir / "garbage.yaml").write_text("not really yaml: [unbalanced", encoding="utf-8")
        (self.test_dir / "ok.yaml").write_text("---\n", encoding="utf-8")

        result = [Path(p).name for p in discover_configs(str(self.test_dir))]
        self.assertEqual(result, ["garbage.yaml", "ok.yaml"])


# ---------------------------------------------------------------------------
# Config directory priority resolution
# ---------------------------------------------------------------------------

class TestConfigDirPriority(unittest.TestCase):
    """main._resolve_config_dir priority: env > ./configs > ~/.sentinel/configs."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.env_dir = self.test_dir / "env_configs"
        self.cwd_dir = self.test_dir / "cwd_root"
        self.home_dir = self.test_dir / "home"
        self.env_dir.mkdir()
        self.cwd_dir.mkdir()
        (self.cwd_dir / "configs").mkdir()
        (self.home_dir / ".sentinel" / "configs").mkdir(parents=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _resolve(self, env_value=None):
        from sentinel import main as main_mod
        env_patch = {"SENTINEL_CONFIGS_DIR": env_value} if env_value else {}
        # Always strip the variable when we want to fall through.
        with patch.dict(os.environ, env_patch, clear=False):
            if not env_value:
                os.environ.pop("SENTINEL_CONFIGS_DIR", None)
            with patch("sentinel.main.Path.cwd", return_value=self.cwd_dir), \
                 patch("sentinel.main.Path.home", return_value=self.home_dir):
                return main_mod._resolve_config_dir()

    def test_env_var_wins_over_cwd_and_home(self):
        result = self._resolve(env_value=str(self.env_dir))
        self.assertEqual(result, self.env_dir)

    def test_cwd_used_when_env_missing(self):
        result = self._resolve(env_value=None)
        self.assertEqual(result, self.cwd_dir / "configs")

    def test_home_used_when_cwd_missing(self):
        # Remove cwd configs so the cwd branch falls through.
        shutil.rmtree(self.cwd_dir / "configs")
        result = self._resolve(env_value=None)
        self.assertEqual(result, self.home_dir / ".sentinel" / "configs")

    def test_returns_none_when_nothing_configured(self):
        shutil.rmtree(self.cwd_dir / "configs")
        shutil.rmtree(self.home_dir / ".sentinel" / "configs")
        result = self._resolve(env_value=None)
        self.assertIsNone(result)

    def test_env_var_pointing_to_missing_dir_falls_through(self):
        result = self._resolve(env_value=str(self.test_dir / "ghost"))
        # Falls through to cwd configs, which exists.
        self.assertEqual(result, self.cwd_dir / "configs")


# ---------------------------------------------------------------------------
# Multi-config processing (skip-and-continue, dry-run)
# ---------------------------------------------------------------------------

class TestMultiConfigProcessing(unittest.TestCase):
    """End-to-end behaviour of main() across multiple configs."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.cfg_dir = self.test_dir / "configs"
        self.cfg_dir.mkdir()
        (self.cfg_dir / "disabled").mkdir()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_dry_run_skips_invalid_and_runs_valid(self):
        from sentinel.main import main

        # Two valid, one invalid (no phases enabled), one disabled-by-convention.
        write_yaml(self.cfg_dir / "alpha.yaml", name="alpha")
        write_yaml(self.cfg_dir / "beta.yaml", name="beta")
        broken = self.cfg_dir / "broken.yaml"
        broken.write_text(VALID_YAML_TEMPLATE.format(name="bad").replace("enabled: true", "enabled: false"), encoding="utf-8")
        write_yaml(self.cfg_dir / "disabled" / "parked.yaml", name="parked")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        out = buf_out.getvalue()
        # Three top-level configs picked up; disabled/parked.yaml ignored.
        self.assertIn("3 config(s) processed", out)
        self.assertIn("2 ok", out)
        self.assertIn("1 skipped", out)
        self.assertIn("0 failed", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertNotIn("parked", out)
        self.assertEqual(rc, 0)

    def test_single_config_validation_error_skipped(self):
        from sentinel.main import main

        path = self.cfg_dir / "broken.yaml"
        path.write_text(VALID_YAML_TEMPLATE.format(name="bad").replace("enabled: true", "enabled: false"), encoding="utf-8")

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = main(["--config", str(path), "--dry-run"])

        self.assertIn("1 config(s) processed", buf.getvalue())
        self.assertIn("1 skipped", buf.getvalue())
        self.assertEqual(rc, 0)

    def test_empty_directory_returns_2_with_warning(self):
        from sentinel.main import main

        empty = self.test_dir / "empty"
        empty.mkdir()

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(empty), "--dry-run"])

        # OP-019: cron / Task Scheduler should be able to alarm on a
        # nightly run that didn't process anything.
        self.assertEqual(rc, 2)
        self.assertIn("no active config files found", buf_err.getvalue().lower())

    def test_duplicate_project_name_returns_2_before_running(self):
        from sentinel.main import main

        # Two configs with the same project.name = "alpha".
        write_yaml(self.cfg_dir / "first.yaml", name="alpha")
        write_yaml(self.cfg_dir / "second.yaml", name="alpha")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        # OP-002: hard error before any phase runs.
        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        self.assertIn("project.name must be unique", err)
        self.assertIn("'alpha'", err)
        self.assertIn("first.yaml", err)
        self.assertIn("second.yaml", err)
        # Aggregate summary must NOT have run — the bail-out is upstream.
        self.assertNotIn("config(s) processed", buf_out.getvalue())

    def test_distinct_project_names_do_not_trigger_collision(self):
        from sentinel.main import main

        write_yaml(self.cfg_dir / "alpha.yaml", name="alpha")
        write_yaml(self.cfg_dir / "beta.yaml", name="beta")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        self.assertEqual(rc, 0)
        self.assertNotIn("project.name must be unique", buf_err.getvalue())
        self.assertIn("2 config(s) processed", buf_out.getvalue())

    def test_collision_detection_is_case_insensitive(self):
        """`alpha` and `ALPHA` collide on every OS.

        On Windows the two would resolve to the same NTFS folder
        (filesystem-level collision). On POSIX the filesystem would
        keep them apart, but the identity-ambiguity rationale (logs
        and summaries refer to projects only by name) still applies.
        Casefold-based matching covers both cases. The error output
        must preserve the operator's declared capitalisation so they
        know which file to edit.
        """
        from sentinel.main import main

        write_yaml(self.cfg_dir / "lower.yaml", name="alpha")
        write_yaml(self.cfg_dir / "upper.yaml", name="ALPHA")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        self.assertIn("project.name must be unique", err)
        # Operator-declared capitalisation is preserved in the report:
        # one of the two original spellings shows up in the heading.
        self.assertTrue("'alpha'" in err or "'ALPHA'" in err,
                        f"expected alpha or ALPHA in error, got:\n{err}")
        self.assertIn("lower.yaml", err)
        self.assertIn("upper.yaml", err)
        # Bail-out happened before the per-config loop.
        self.assertNotIn("config(s) processed", buf_out.getvalue())

    def test_missing_single_config_returns_2_not_systemexit(self):
        """main(['--config', '<ghost>']) returns 2 instead of raising
        SystemExit, so callers that invoke main() as a function
        (tests, future supervisor processes, plugin hosts) get a
        normal int back. The __main__ shim is the only place that
        translates the return value into a process exit.
        """
        from sentinel.main import main

        ghost = self.test_dir / "definitely-missing.yaml"

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config", str(ghost), "--dry-run"])

        self.assertEqual(rc, 2)
        self.assertIn("config file not found", buf_err.getvalue())

    def test_config_pointing_at_directory_suggests_config_dir(self):
        """`--config <some_dir>` would historically report
        "config file not found" (technically true: it's not a file),
        which is misleading and offers no way out. Distinguish the
        two cases explicitly and nudge the operator toward the
        correct flag.
        """
        from sentinel.main import main

        # An *existing* directory passed where --config wants a file.
        existing_dir = self.test_dir / "actually-a-dir"
        existing_dir.mkdir()

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config", str(existing_dir), "--dry-run"])

        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        self.assertIn("expects a YAML file", err)
        self.assertIn("is a directory", err)
        # Hint the operator at the correct flag.
        self.assertIn("--config-dir", err)
        # The bare "config file not found" message must NOT show — that
        # was the previous, misleading wording.
        self.assertNotIn("config file not found", err)

    def test_oserror_on_config_stat_returns_2_not_traceback(self):
        """Path.is_file() in _list_configs can raise OSError on a
        permission-denied parent dir or a transient FS error. Without
        the wrap, that propagates out and crashes main(); with it, we
        get the same clean exit-code-2 contract as every other
        misconfiguration path.
        """
        from unittest.mock import patch
        from sentinel.main import main

        leaky = PermissionError(13, "Permission denied")
        # Mock Path.is_file globally so the patched call covers the
        # one in _list_configs without affecting unrelated callers
        # within main (none reach this codepath in dry-run).
        with patch("pathlib.Path.is_file", side_effect=leaky):
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                rc = main(["--config", "/some/locked.yaml", "--dry-run"])

        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        self.assertIn("could not access config file", err)
        self.assertIn("Permission denied", err)
        self.assertIn("/some/locked.yaml", err)

    def test_oserror_on_config_dir_stat_returns_2_not_traceback(self):
        from unittest.mock import patch
        from sentinel.main import main

        leaky = PermissionError(13, "Permission denied")
        with patch("pathlib.Path.is_dir", side_effect=leaky):
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            with redirect_stdout(buf_out), redirect_stderr(buf_err):
                rc = main(["--config-dir", "/some/locked-dir", "--dry-run"])

        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        self.assertIn("could not access config directory", err)
        self.assertIn("Permission denied", err)
        self.assertIn("/some/locked-dir", err)

    def test_missing_config_dir_returns_2_not_systemexit(self):
        from sentinel.main import main

        ghost = self.test_dir / "definitely-missing-dir"

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(ghost), "--dry-run"])

        self.assertEqual(rc, 2)
        self.assertIn("config directory not found", buf_err.getvalue())

    def test_collision_note_indents_multiline_load_errors(self):
        """MarkedYAMLError messages span several lines; the note
        section must indent continuations to align with the bullet
        body or the output becomes unreadable.
        """
        from sentinel.main import main

        # Two configs colliding on project.name = "alpha".
        write_yaml(self.cfg_dir / "first.yaml", name="alpha")
        write_yaml(self.cfg_dir / "second.yaml", name="alpha")
        # Sibling with a real YAML parse error → multi-line message.
        broken = self.cfg_dir / "broken.yaml"
        broken.write_text("foo: [unbalanced\n", encoding="utf-8")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        self.assertEqual(rc, 2)
        err_lines = buf_err.getvalue().splitlines()
        # Locate the bullet for broken.yaml in the "note:" block.
        bullets = [
            i for i, line in enumerate(err_lines)
            if line.startswith("  - ") and "broken.yaml" in line
        ]
        self.assertEqual(len(bullets), 1, f"expected one bullet, got: {err_lines}")
        bullet_idx = bullets[0]
        # The first continuation line MUST start with the 6-space
        # indent (2 of bullet + 4 of "- ") — never at column 0.
        self.assertGreater(len(err_lines), bullet_idx + 1,
                           "multi-line YAML error should produce continuations")
        for cont in err_lines[bullet_idx + 1:]:
            if not cont.strip():
                continue
            if cont.startswith("  - "):
                # Hit the next bullet, stop scanning.
                break
            self.assertTrue(
                cont.startswith("      "),
                f"continuation line not properly indented: {cont!r}",
            )

    def test_collision_with_unreadable_sibling_surfaces_both(self):
        """When the runner aborts on a collision, unreadable configs
        in the same directory are surfaced too.

        The collision exit short-circuits the per-config loop where
        ``_process_config`` would otherwise have reported the
        unreadable file as a "skipped (load error)" entry. To avoid
        the silent drop, ``_check_project_name_collisions`` buffers
        load failures and ``main`` prints them in a "note:" block
        right after the collision listing. Operators thus see every
        file that needs attention, not just the colliding pair.
        """
        from sentinel.main import main

        # Two configs colliding on project.name = "alpha".
        write_yaml(self.cfg_dir / "first.yaml", name="alpha")
        write_yaml(self.cfg_dir / "second.yaml", name="alpha")
        # One unrelated file with raw non-UTF-8 bytes.
        unreadable = self.cfg_dir / "binary.yaml"
        unreadable.write_bytes(b"\xff\xfe\x00garbage\x80\x81")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        self.assertEqual(rc, 2)
        err = buf_err.getvalue()
        # Collision listing — both colliding files visible.
        self.assertIn("project.name must be unique", err)
        self.assertIn("first.yaml", err)
        self.assertIn("second.yaml", err)
        # Note section — the unreadable file shows up despite the
        # short-circuit.
        self.assertIn("could not be checked for collisions", err)
        self.assertIn("binary.yaml", err)
        # Bail-out happened before the per-config loop.
        self.assertNotIn("config(s) processed", buf_out.getvalue())

    def test_unreadable_config_does_not_crash_pre_scan(self):
        """A non-UTF-8 config must not crash the collision pre-scan.

        Before this hardening, `load_config_file` only wrapped
        `yaml.YAMLError`; a `UnicodeDecodeError` raised while reading
        the file would propagate out of `_check_project_name_collisions`
        and abort the runner with a traceback before the per-config
        loop ever started. The bad file should now be reported as a
        normal "skipped" entry — same bucket as a config that fails
        validation — and its valid sibling should still run.
        """
        from sentinel.main import main

        write_yaml(self.cfg_dir / "good.yaml", name="alpha")
        bad = self.cfg_dir / "binary.yaml"
        # Raw non-UTF-8 bytes (UTF-16 BOM + arbitrary garbage).
        bad.write_bytes(b"\xff\xfe\x00\x42\xff\xff\x00garbage\x80\x81\x82")

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        out = buf_out.getvalue()
        # Pre-scan survived. Both files were processed (one ok, one skipped).
        self.assertIn("2 config(s) processed", out)
        self.assertIn("1 ok", out)
        self.assertIn("1 skipped", out)
        self.assertEqual(rc, 0)

    def test_collision_scan_skips_non_mapping_project_block(self):
        """A YAML with `project: alpha` (scalar, not a mapping) parses
        but is structurally invalid. The collision pre-scan must skip
        it instead of crashing the whole runner with AttributeError;
        the schema violation surfaces later as a normal "skipped
        (validation)" entry from _process_config.
        """
        from sentinel.main import main

        # One valid config + one with a non-mapping project value.
        write_yaml(self.cfg_dir / "good.yaml", name="alpha")
        bad = self.cfg_dir / "bad.yaml"
        bad.write_text(
            "schema_version: \"1.0\"\nproject: alpha\nfetch:\n  type: git\n"
            "  url: https://example/x.git\nproject_manifest: m.txt\n"
            "phases:\n  synthesis:\n    enabled: true\n",
            encoding="utf-8",
        )

        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = main(["--config-dir", str(self.cfg_dir), "--dry-run"])

        # Pre-scan did NOT crash. The bad config is reported as skipped
        # by the per-config validation step.
        self.assertIn("2 config(s) processed", buf_out.getvalue())
        self.assertIn("1 skipped", buf_out.getvalue())
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Fetch phase (new schema)
# ---------------------------------------------------------------------------

class TestFetchCodePhase(unittest.TestCase):
    """fetch_code() against the new top-level fetch block."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _ctx(self):
        from sentinel import RunContext
        return RunContext.for_run(self.run_dir)

    def _git_config(self, **fetch_overrides):
        cfg = {
            "fetch": {
                "type": "git",
                "url": "https://github.com/test/repo.git",
                "branch": "main",
                "shallow_clone": True,
                "depth": 1,
            },
        }
        cfg["fetch"].update(fetch_overrides)
        return cfg

    @patch("sentinel.fetch_code.HAS_GITPYTHON", new=False)
    @patch("sentinel.fetch_code.subprocess.run")
    def test_git_clone_invokes_git_with_expected_args(self, mock_subprocess):
        from sentinel.fetch_code import fetch_code

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = fetch_code(self._git_config(), self._ctx())

        self.assertIsNotNone(result)
        self.assertTrue(mock_subprocess.called)
        argv = mock_subprocess.call_args[0][0]
        self.assertIn("git", argv[0])
        self.assertIn("clone", argv)
        self.assertIn("--branch", argv)
        self.assertIn("main", argv)

    def test_local_copy_round_trip(self):
        from sentinel.fetch_code import fetch_code

        source = self.test_dir / "sources"
        source.mkdir()
        (source / "design.vhd").write_text("-- vhdl\n", encoding="utf-8")

        cfg = {"fetch": {"type": "local", "local_path": str(source)}}
        ctx = self._ctx()
        result = fetch_code(cfg, ctx)

        self.assertIsNotNone(result)
        self.assertTrue((Path(result) / "design.vhd").exists())
        self.assertEqual(str(ctx.repo_path), result)

    def test_local_path_missing_returns_none(self):
        from sentinel.fetch_code import fetch_code

        cfg = {"fetch": {"type": "local", "local_path": str(self.test_dir / "ghost")}}
        self.assertIsNone(fetch_code(cfg, self._ctx()))


# ---------------------------------------------------------------------------
# Fetch / SSH key escaping (OP-009)
# ---------------------------------------------------------------------------

class TestGitSshCommandEscaping(unittest.TestCase):
    """_build_git_ssh_command quotes the key path so paths containing
    shell metacharacters (spaces, quotes, $, etc.) survive intact in
    GIT_SSH_COMMAND. Otherwise the env var becomes malformed and git
    either silently degrades to the default SSH agent or hands ssh an
    unexpected argument list.
    """

    def setUp(self):
        self._logger = logging.getLogger("sentinel.tests.ssh")

    def _build(self, key_path):
        """Drive _build_git_ssh_command with `os.path.exists` mocked
        True so a synthetic path is treated as present without our
        having to materialise it.
        """
        from unittest.mock import patch
        from sentinel.fetch_code import _build_git_ssh_command

        with patch("sentinel.fetch_code.os.path.exists", return_value=True):
            return _build_git_ssh_command(key_path, self._logger)

    def _key_arg(self, command):
        """Extract the argument that follows ``-i`` after the value
        has gone through the shell-style parsing git itself would do.
        """
        tokens = shlex.split(command)
        i = tokens.index("-i")
        return tokens[i + 1]

    def test_plain_path_round_trips(self):
        key = "/home/me/.ssh/id_ed25519_sentinel"
        cmd = self._build(key)
        self.assertEqual(self._key_arg(cmd), key)

    def test_path_with_spaces_round_trips(self):
        key = "/home/me/with spaces/key"
        cmd = self._build(key)
        self.assertEqual(self._key_arg(cmd), key)

    def test_path_with_double_quote_does_not_break_env_var(self):
        key = '/home/me/with"dq/key'
        cmd = self._build(key)
        # Pre-fix this path produced f'ssh -i "/home/me/with"dq/key" -o ...',
        # which truncates the -i argument at the embedded double quote
        # and feeds `dq/key` to ssh as a positional arg. shlex.split on
        # the new value must produce the original key verbatim.
        self.assertEqual(self._key_arg(cmd), key)

    def test_path_with_single_quote_round_trips(self):
        key = "/home/me/with'sq/key"
        cmd = self._build(key)
        self.assertEqual(self._key_arg(cmd), key)

    def test_path_with_dollar_sign_is_not_expanded(self):
        # Pre-fix used double quotes, which let git's shell-style
        # parser see $HOME / $SSH_AUTH_SOCK as variable references.
        # shlex.quote uses single quotes so $ stays literal.
        key = "/home/me/dollar$sign/key"
        cmd = self._build(key)
        self.assertEqual(self._key_arg(cmd), key)

    def test_command_includes_identitiesonly_yes(self):
        """The IdentitiesOnly=yes guard must stay — without it, ssh
        ignores -i if a matching key is loaded in the agent.
        """
        cmd = self._build("/home/me/.ssh/id_ed25519")
        self.assertIn("IdentitiesOnly=yes", cmd)

    def test_missing_key_returns_none_and_warns(self):
        from unittest.mock import patch
        from sentinel.fetch_code import _build_git_ssh_command

        # _build_git_ssh_command emits via the logger the caller
        # passes in (the function does not own its own logger). We
        # capture on that logger's name explicitly so assertLogs
        # sees the record regardless of the test logger's hierarchy.
        with patch("sentinel.fetch_code.os.path.exists", return_value=False):
            with self.assertLogs(self._logger.name, level="WARNING") as cap:
                result = _build_git_ssh_command("/no/such/key", self._logger)

        self.assertIsNone(result)
        # The warning is the only operator-facing signal that the run
        # silently degraded to the default SSH agent — its absence
        # would mask the misconfiguration.
        joined = "\n".join(cap.output)
        self.assertIn("falling back to default agent", joined)

    def test_no_key_configured_returns_none(self):
        from sentinel.fetch_code import _build_git_ssh_command

        self.assertIsNone(_build_git_ssh_command(None, self._logger))
        self.assertIsNone(_build_git_ssh_command("", self._logger))


# ---------------------------------------------------------------------------
# Synthesis (YAML schema, RunContext-aware)
# ---------------------------------------------------------------------------

class TestSynthesisPhase(unittest.TestCase):
    """run_synthesis() against the current YAML / RunContext API."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()

        # The current code requires synthesis_tool_path to exist on disk
        # before invoking the vendor binary; stand it up as an empty dir.
        self.tool_dir = self.test_dir / "vivado_install"
        self.tool_dir.mkdir()

        # Stage a fetched repo with a discoverable synthesis script.
        self.repo_dir = self.test_dir / "repo"
        scripts_dir = self.repo_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "run_synthesis.tcl").write_text(
            "# vivado synthesis script\nexit 0\n", encoding="utf-8"
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _ctx(self, with_repo: bool = True):
        from sentinel import RunContext
        ctx = RunContext.for_run(self.run_dir)
        if with_repo:
            ctx.repo_path = self.repo_dir
        return ctx

    def _vivado_config(self, **synthesis_overrides):
        cfg = {
            "phases": {
                "synthesis": {
                    "enabled": True,
                    "synthesis_tool": "vivado",
                    "synthesis_tool_path": str(self.tool_dir),
                    "output_dir": "vivado_synth",
                }
            }
        }
        cfg["phases"]["synthesis"].update(synthesis_overrides)
        return cfg

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_success_invokes_batch_with_autodiscovered_script(self, mock_subprocess):
        from sentinel.synthesis import run_synthesis

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        result = run_synthesis(self._vivado_config(), self._ctx())

        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(result.get("tool"), "vivado")
        mock_subprocess.assert_called_once()
        cmd = mock_subprocess.call_args[0][0]
        self.assertEqual(cmd[0], "vivado")
        self.assertIn("-mode", cmd)
        self.assertIn("batch", cmd)
        self.assertIn("-source", cmd)
        # Auto-discovered the script we staged under <repo>/scripts/.
        source_idx = cmd.index("-source")
        self.assertTrue(cmd[source_idx + 1].endswith("run_synthesis.tcl"))

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_uses_explicit_repo_synthesis_script_when_set(self, mock_subprocess):
        from sentinel.synthesis import run_synthesis

        # Place a custom-named script the auto-discovery would not pick.
        custom = self.repo_dir / "build" / "custom_syn.tcl"
        custom.parent.mkdir(parents=True)
        custom.write_text("# custom script\n", encoding="utf-8")

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        cfg = self._vivado_config(repo_synthesis_script="build/custom_syn.tcl")
        result = run_synthesis(cfg, self._ctx())

        self.assertEqual(result.get("status"), "completed")
        cmd = mock_subprocess.call_args[0][0]
        source_idx = cmd.index("-source")
        self.assertEqual(Path(cmd[source_idx + 1]).name, "custom_syn.tcl")

    @patch("sentinel.synthesis.subprocess.run")
    def test_synthesis_logs_path_prepend_when_tool_bin_exists(self, mock_run):
        """OP-036: the backend used to log
        ``Environment: VIVADO_PATH=<tool_path>`` unconditionally,
        even though the code prepends ``<tool_path>/bin`` to ``PATH``
        and never touches a ``VIVADO_PATH`` env var. The misleading
        label is replaced by a ``Prepending <bin> to PATH`` line that
        only fires when the bin directory actually exists.
        """
        from sentinel.synthesis import run_synthesis

        # Stage <tool_dir>/bin so the prepend branch fires.
        bin_dir = self.tool_dir / "bin"
        bin_dir.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        with self.assertLogs("sentinel.synthesis", level="INFO") as cap:
            result = run_synthesis(self._vivado_config(), self._ctx())

        log_text = "\n".join(cap.output)
        self.assertEqual(result.get("status"), "completed")
        self.assertIn("Prepending", log_text)
        self.assertIn(str(bin_dir), log_text)
        self.assertIn("PATH", log_text)
        # The old, misleading label must not appear anywhere.
        self.assertNotIn("VIVADO_PATH", log_text)

    @patch("sentinel.synthesis.subprocess.run")
    def test_synthesis_skips_path_log_when_tool_bin_missing(self, mock_run):
        """OP-036: when ``<tool_path>/bin`` does not exist (e.g. a
        non-standard install layout), the backend leaves ``PATH``
        untouched — and must therefore NOT log "Prepending ... to
        PATH", which would be just as misleading as the old
        "VIVADO_PATH=" line was. setUp leaves tool_dir without a bin/
        child, so this exercises the skip branch.
        """
        from sentinel.synthesis import run_synthesis

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        with self.assertLogs("sentinel.synthesis", level="INFO") as cap:
            run_synthesis(self._vivado_config(), self._ctx())

        log_text = "\n".join(cap.output)
        self.assertNotIn("Prepending", log_text)
        self.assertNotIn("VIVADO_PATH", log_text)

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_failure_error_field_takes_stderr_tail(self, mock_run):
        """OP-015: vendor stderr usually starts with a banner /
        license dump / version line; the actual failing message
        is at the tail. The previous ``result.stderr[:500]`` clipped
        away the diagnostic and kept the banner. Switch to
        ``result.stderr[-500:]`` so the snippet shown in summaries
        is the useful tail.
        """
        from sentinel.synthesis import run_synthesis

        # Stage <tool_dir>/bin so the PATH branch fires (cosmetic;
        # not the focus of this test).
        (self.tool_dir / "bin").mkdir()

        banner = "BANNER_TO_DROP\n" * 5  # ~75 chars of leading noise
        # Real diagnostic block, padded so it fully occupies the
        # final 500-char window — guarantees the tail slice excludes
        # the banner regardless of total length.
        real_error_block = (
            "ERROR: [Synth 8-5535] port 'clk' missing in module 'top'\n"
        ) * 10  # ~570 chars
        stderr = banner + real_error_block  # ~645 chars

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=stderr,
        )

        result = run_synthesis(self._vivado_config(), self._ctx())

        self.assertEqual(result.get("status"), "failed")
        # The actual error must survive the truncation; the banner
        # noise must not.
        self.assertIn("Synth 8-5535", result.get("error", ""))
        self.assertNotIn("BANNER_TO_DROP", result.get("error", ""))
        # Cap is still 500 chars.
        self.assertLessEqual(len(result.get("error", "")), 500)

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_failure_short_stderr_preserved_in_full(self, mock_run):
        """When stderr is shorter than 500 chars the tail-slice
        becomes the whole string, so a small but complete error
        message is preserved verbatim — no off-by-one truncation.
        """
        from sentinel.synthesis import run_synthesis

        (self.tool_dir / "bin").mkdir()
        short_err = "ERROR: place_design failed at site X42Y90"

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=short_err,
        )

        result = run_synthesis(self._vivado_config(), self._ctx())

        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error"), short_err)

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_failure_returns_full_log_path(self, mock_run):
        """OP-015 part two: the return dict references the combined
        log file (`synthesis_full.log`, which captures the full
        STDOUT + STDERR), so the 500-char truncation in ``error``
        is no longer load-bearing — operators can always tail the
        full output from that path.
        """
        from sentinel.synthesis import run_synthesis

        (self.tool_dir / "bin").mkdir()
        mock_run.return_value = MagicMock(
            returncode=2, stdout="some-stdout", stderr="some-stderr",
        )

        result = run_synthesis(self._vivado_config(), self._ctx())

        full_log = result.get("full_log_file")
        self.assertIsNotNone(full_log, "full_log_file must be in the return dict")
        self.assertTrue(Path(full_log).exists(), full_log)
        # File must contain both streams under labelled sections.
        contents = Path(full_log).read_text(encoding="utf-8")
        self.assertIn("=== STDOUT ===", contents)
        self.assertIn("=== STDERR ===", contents)
        self.assertIn("some-stdout", contents)
        self.assertIn("some-stderr", contents)

    @patch("sentinel.synthesis.subprocess.run")
    def test_vivado_timeout_returns_failed_with_timeout_error(self, mock_subprocess):
        from sentinel.synthesis import run_synthesis

        mock_subprocess.side_effect = subprocess.TimeoutExpired("vivado", 3600)

        result = run_synthesis(self._vivado_config(), self._ctx())

        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("error"), "Timeout")

    def test_synthesis_disabled_returns_skipped(self):
        from sentinel.synthesis import run_synthesis

        cfg = {"phases": {"synthesis": {"enabled": False}}}
        result = run_synthesis(cfg, self._ctx(with_repo=False))
        self.assertEqual(result.get("status"), "skipped")

    def test_synthesis_unsupported_tool_returns_error_dict(self):
        from sentinel.synthesis import run_synthesis

        cfg = {
            "phases": {
                "synthesis": {
                    "enabled": True,
                    "synthesis_tool": "unsupported_tool",
                }
            }
        }
        result = run_synthesis(cfg, self._ctx(with_repo=False))
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Unsupported", result.get("message", ""))

    def test_synthesis_no_fetched_repo_surfaces_targeted_error(self):
        """OP-034: when ``ctx.repo_path`` is None (fetch disabled or
        failed) AND the ``repos_root`` placeholder is empty/missing,
        the synthesis backend must say so explicitly instead of
        falling through to "Repository synthesis script not found" —
        which would look like a misconfigured script path rather than
        the missing-repo it actually is.
        """
        from sentinel import RunContext
        from sentinel.synthesis import run_synthesis

        ctx = RunContext.for_run(self.run_dir)
        # ctx.repo_path stays None — no fetch phase ran.

        result = run_synthesis(self._vivado_config(), ctx)

        self.assertEqual(result.get("status"), "error")
        self.assertIn("No repository available", result.get("message", ""))
        self.assertIn("fetch phase", result.get("message", ""))

    @patch("sentinel.synthesis.subprocess.run")
    def test_synthesis_uses_pre_staged_repos_root_when_repo_path_unset(
        self, mock_subprocess
    ):
        """OP-034 fallback contract: callers that pre-stage sources
        under ``ctx.repos_root`` without setting ``ctx.repo_path`` must
        still be able to drive synthesis. The targeted "no repository
        available" guard should only fire on an *empty* placeholder —
        a populated one falls through to the existing autodiscovery.
        """
        from sentinel import RunContext
        from sentinel.synthesis import run_synthesis

        # Pre-stage scripts/run_synthesis.tcl directly under repos_root.
        # ctx.repo_root will resolve to repos_root because repo_path is
        # unset, so autodiscovery finds the script there.
        scripts_dir = self.run_dir / "repos" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "run_synthesis.tcl").write_text(
            "# pre-staged synthesis script\nexit 0\n", encoding="utf-8"
        )

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        ctx = RunContext.for_run(self.run_dir)  # repo_path left None
        result = run_synthesis(self._vivado_config(), ctx)

        # Guard did NOT abort the run; autodiscovery found the script.
        self.assertNotEqual(result.get("status"), "error")
        self.assertNotIn("No repository available", result.get("message", "") or "")
        mock_subprocess.assert_called_once()

    def test_synthesis_missing_tool_path_returns_error(self):
        from sentinel.synthesis import run_synthesis

        cfg = {
            "phases": {
                "synthesis": {
                    "enabled": True,
                    "synthesis_tool": "vivado",
                    # synthesis_tool_path deliberately omitted
                }
            }
        }
        result = run_synthesis(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("synthesis_tool_path", result.get("message", ""))


# ---------------------------------------------------------------------------
# Linting phase (OP-040): aurig-lint project lint runner via subprocess
# ---------------------------------------------------------------------------

class TestLintingPhase(unittest.TestCase):
    """run_linting() against the aurig-lint project lint runner."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()

        # Stage a aurig-lint checkout with the runner script in place.
        self.aurig_lint_dir = self.test_dir / "aurig-lint"
        (self.aurig_lint_dir / "tools").mkdir(parents=True)
        (self.aurig_lint_dir / "tools" / "run_lint_project_inprocess.tcl").write_text(
            "# fake project lint runner\n", encoding="utf-8"
        )

        # Stage a fetched repo so the OP-034 guard passes.
        self.repo_dir = self.test_dir / "repo"
        self.repo_dir.mkdir()
        (self.repo_dir / "manifest.yaml").write_text(
            "project_name: demo\n", encoding="utf-8"
        )

        # Make sure the env var doesn't leak in from outside.
        self._prev_env = os.environ.pop("SENTINEL_AURIG_LINT_PATH", None)

    def tearDown(self):
        if self._prev_env is not None:
            os.environ["SENTINEL_AURIG_LINT_PATH"] = self._prev_env
        else:
            os.environ.pop("SENTINEL_AURIG_LINT_PATH", None)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _ctx(self, with_repo: bool = True):
        from sentinel import RunContext
        ctx = RunContext.for_run(self.run_dir)
        if with_repo:
            ctx.repo_path = self.repo_dir
        return ctx

    def _config(self, project_manifest="manifest.yaml", **linting_overrides):
        cfg = {
            "project_manifest": project_manifest,
            "phases": {
                "linting": {
                    "enabled": True,
                    "aurig_lint_path": str(self.aurig_lint_dir),
                }
            },
        }
        cfg["phases"]["linting"].update(linting_overrides)
        return cfg

    # ---- disabled / config-error paths ----

    def test_phase_disabled_returns_skipped(self):
        from sentinel.linting import run_linting
        cfg = self._config(enabled=False)
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "skipped")

    def test_missing_aurig_lint_path_in_config_and_env_returns_error(self):
        from sentinel.linting import run_linting
        # No aurig_lint_path field, env var cleared in setUp.
        cfg = {
            "project_manifest": "manifest.yaml",
            "phases": {"linting": {"enabled": True}},
        }
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("aurig_lint_path", result.get("message", ""))

    def test_env_var_provides_aurig_lint_path_when_yaml_field_absent(self):
        from sentinel.linting import run_linting
        os.environ["SENTINEL_AURIG_LINT_PATH"] = str(self.aurig_lint_dir)
        cfg = {
            "project_manifest": "manifest.yaml",
            "phases": {"linting": {"enabled": True}},
        }
        # We only verify the env-var resolution path; intercept subprocess.run
        # so we don't actually exec tclsh.
        with patch("sentinel.linting.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(mock_run.call_count, 1)

    def test_aurig_lint_path_not_a_directory_returns_error(self):
        from sentinel.linting import run_linting
        bogus = self.test_dir / "nonexistent_aurig_lint"
        cfg = self._config(aurig_lint_path=str(bogus))
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("aurig_lint_path", result.get("message", ""))

    def test_missing_runner_script_returns_error(self):
        from sentinel.linting import run_linting
        # Remove the runner script so aurig_lint_path is a directory but
        # the runner isn't where Sentinel expects it.
        (self.aurig_lint_dir / "tools" / "run_lint_project_inprocess.tcl").unlink()
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn(
            "run_lint_project_inprocess.tcl", result.get("message", "")
        )

    def test_no_repository_with_fetch_attempted_surfaces_targeted_error(self):
        from sentinel.linting import run_linting
        ctx = self._ctx(with_repo=False)
        ctx.fetch_attempted = True
        # repo_root may exist as an empty placeholder; force the
        # "fetch ran but produced nothing" branch by clearing repo_path
        # and setting fetch_attempted.
        result = run_linting(self._config(), ctx)
        self.assertEqual(result.get("status"), "error")
        self.assertIn(
            "Fetch phase ran but produced no usable repository",
            result.get("message", ""),
        )

    # ---- success / failure / error mapping ----

    @patch("sentinel.linting.subprocess.run")
    def test_lint_success_returns_completed(self, mock_run):
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(
            returncode=0, stdout="clean", stderr="",
        )
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "completed")
        self.assertEqual(result.get("exit_code"), 0)
        self.assertTrue(result.get("output_dir"))
        # The command must include the manifest and the runner script.
        cmd = mock_run.call_args[0][0]
        self.assertIn("-manifest", cmd)
        manifest_idx = cmd.index("-manifest")
        self.assertTrue(cmd[manifest_idx + 1].endswith("manifest.yaml"))
        self.assertTrue(any(
            arg.endswith("run_lint_project_inprocess.tcl") for arg in cmd
        ))

    @patch("sentinel.linting.subprocess.run")
    def test_lint_findings_return_failed_with_stderr_tail(self, mock_run):
        """rc=1 from aurig-lint means diagnostics ≥ fail_on threshold.
        Sentinel maps that onto ``failed`` so ``continue_on_error``
        controls whether the next phase runs.
        """
        from sentinel.linting import run_linting
        # Banner-style stderr to confirm the OP-015-style tail slice.
        banner = "BANNER\n" * 5
        tail = "ERROR: signal_naming on `BadCaps` at file.vhd:42\n" * 20
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=banner + tail,
        )
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "failed")
        self.assertEqual(result.get("exit_code"), 1)
        # The diagnostic tail must survive; the banner must not.
        self.assertIn("signal_naming", result.get("error", ""))
        self.assertNotIn("BANNER", result.get("error", ""))
        self.assertLessEqual(len(result.get("error", "")), 500)
        # full_log_file must be set so OP-015-style downstream
        # inspection is possible.
        self.assertTrue(result.get("full_log_file"))

    @patch("sentinel.linting.subprocess.run")
    def test_lint_tool_error_returns_error_status(self, mock_run):
        """rc=2 from aurig-lint is a tool error (broken setup, malformed
        manifest, etc.) — distinct from a code-quality failure. Sentinel
        maps it onto ``error`` so operators see "broken setup" rather
        than "lint regression".
        """
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(
            returncode=2, stdout="", stderr="tool error: cannot parse YAML",
        )
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertEqual(result.get("exit_code"), 2)
        self.assertIn("tool error", result.get("error", "").lower())

    @patch("sentinel.linting.subprocess.run")
    def test_unexpected_exit_code_returns_error_not_failed(self, mock_run):
        """aurig-lint's documented contract is 0/1/2. An unexpected
        non-zero code (aurig-lint bug, tclsh crash, future contract
        drift) is a tool/setup issue, not a code-quality regression.
        Map it to ``error`` like rc=2, so the operator does not see
        "lint failed" for what is really "broken tool".
        """
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(
            returncode=99, stdout="", stderr="something unexpected",
        )
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertEqual(result.get("exit_code"), 99)
        # The diagnostic includes the unexpected code so it is
        # immediately visible to whoever reads the summary.
        self.assertIn("99", result.get("error", ""))

    @patch("sentinel.linting.subprocess.run")
    def test_lint_timeout_returns_failed(self, mock_run):
        from sentinel.linting import run_linting
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tclsh", timeout=1800)
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("Timeout", result.get("error", ""))

    @patch("sentinel.linting.subprocess.run")
    def test_lint_tclsh_not_found_returns_failed(self, mock_run):
        from sentinel.linting import run_linting
        mock_run.side_effect = FileNotFoundError("tclsh not on PATH")
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("tclsh", result.get("error", ""))

    # ---- CLI passthrough ----

    @patch("sentinel.linting.subprocess.run")
    def test_optional_policy_include_exclude_passed_through(self, mock_run):
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cfg = self._config(
            policy="lint/lint_user.json",
            include=r".*\.vhd$",
            exclude=r".*generated.*",
            fail_on="warning",
            format="md",
        )
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "completed")
        cmd = mock_run.call_args[0][0]
        # All three optional flags appear with values. The relative
        # policy path is resolved to an absolute path under the repo
        # root (covered in detail by
        # ``test_policy_relative_path_resolved_under_repo_root``);
        # here we just verify the flag is wired up alongside include
        # and exclude.
        self.assertIn("-policy", cmd)
        self.assertTrue(cmd[cmd.index("-policy") + 1].endswith("lint_user.json"))
        self.assertIn("-include", cmd)
        self.assertEqual(cmd[cmd.index("-include") + 1], r".*\.vhd$")
        self.assertIn("-exclude", cmd)
        self.assertEqual(cmd[cmd.index("-exclude") + 1], r".*generated.*")
        # Non-default fail_on and format are passed through.
        self.assertIn("-fail_on", cmd)
        self.assertEqual(cmd[cmd.index("-fail_on") + 1], "warning")
        self.assertIn("-format", cmd)
        self.assertEqual(cmd[cmd.index("-format") + 1], "md")

    @patch("sentinel.linting.subprocess.run")
    def test_optional_flags_omitted_when_unconfigured(self, mock_run):
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "completed")
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("-policy", cmd)
        self.assertNotIn("-include", cmd)
        self.assertNotIn("-exclude", cmd)

    # ---- Copilot PR #17 review hardening ----

    def test_manifest_with_dotdot_escaping_repo_root_returns_error(self):
        """A project_manifest with ``..`` segments must not let
        Sentinel hand the subprocess a path outside the fetched repo.
        """
        from sentinel.linting import run_linting
        # Stage a sibling file outside repo_dir to confirm the escape
        # would otherwise succeed.
        outside = self.test_dir / "outside_manifest.yaml"
        outside.write_text("escaped: true\n", encoding="utf-8")
        cfg = self._config(project_manifest="../outside_manifest.yaml")
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("outside the repo root", result.get("message", ""))

    def test_manifest_absolute_path_outside_repo_root_returns_error(self):
        """An absolute ``project_manifest`` that lands outside the
        repo is rejected with the same targeted message as the
        ``..`` traversal case.
        """
        from sentinel.linting import run_linting
        outside = self.test_dir / "outside_manifest.yaml"
        outside.write_text("escaped: true\n", encoding="utf-8")
        cfg = self._config(project_manifest=str(outside))
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("outside the repo root", result.get("message", ""))

    def test_missing_manifest_file_returns_targeted_error(self):
        """When the manifest path resolves under repo_root but the
        file is absent, surface a targeted error before invoking
        ``tclsh`` (the aurig-lint runner would also fail, but with a
        less specific message).
        """
        from sentinel.linting import run_linting
        cfg = self._config(project_manifest="does_not_exist.yaml")
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("project_manifest not found", result.get("message", ""))

    def test_output_dir_with_dotdot_returns_error_at_runtime(self):
        """Defensive runtime backstop for callers that bypass the
        validator (e.g. ad-hoc test fixtures). The validator catches
        the same case at config-load time — see
        ``TestLintingSchemaValidation.test_rejects_output_dir_with_dotdot``.
        """
        from sentinel.linting import run_linting
        cfg = self._config(output_dir="../escape")
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("output_dir", result.get("message", ""))
        self.assertIn("..", result.get("message", ""))

    def test_output_dir_dot_returns_error_at_runtime(self):
        """``output_dir: "."`` would resolve under
        ``RunContext.output_dir`` to the run directory itself.
        Letting that through has ``package_artifacts`` later try to
        ``copytree`` the run dir into ``<run_dir>/artifacts/<run name>``,
        breaking the bundle step. Reject at the runtime backstop,
        mirroring the validator-side rejection in
        ``TestLintingSchemaValidation.test_rejects_dot_output_dir``.
        """
        from sentinel.linting import run_linting
        result = run_linting(self._config(output_dir="."), self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("output_dir", result.get("message", ""))

    @patch("sentinel.linting.subprocess.run")
    def test_timeout_writes_lint_log_with_diagnostic_summary(self, mock_run):
        """OP-040 contract refinement: ``<run_dir>/logs/lint.log``
        must exist on every exit path, including TimeoutExpired,
        so operators have one consistent place to read after a
        failed nightly. Pre-fix the timeout branch returned without
        writing the log.
        """
        from sentinel.linting import run_linting
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="tclsh", timeout=1800,
        )
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "failed")
        log_file = result.get("log_file")
        self.assertTrue(log_file, "TimeoutExpired path must return a log_file path")
        self.assertTrue(Path(log_file).is_file(), "log_file path must exist on disk")
        log_text = Path(log_file).read_text(encoding="utf-8")
        self.assertIn("TIMEOUT", log_text)
        self.assertIn("Command:", log_text)

    @patch("sentinel.linting.subprocess.run")
    def test_policy_relative_path_resolved_under_repo_root(self, mock_run):
        """``policy`` is documented as a path relative to the fetched
        repo root (matching the ``project_manifest`` convention). The
        subprocess must see an absolute path so a config that works on
        one workstation behaves identically regardless of the CWD
        Sentinel was launched from.
        """
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        cfg = self._config(policy="lint/lint_user.json")
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "completed")
        cmd = mock_run.call_args[0][0]
        policy_idx = cmd.index("-policy")
        passed_policy = cmd[policy_idx + 1]
        # The policy path that reaches aurig-lint must be absolute and
        # rooted at the fetched repo, not the test CWD.
        self.assertTrue(
            Path(passed_policy).is_absolute(),
            f"policy must be resolved to absolute, got {passed_policy!r}",
        )
        self.assertEqual(
            Path(passed_policy).parent.parent.resolve(),
            self.repo_dir.resolve(),
            f"policy must resolve under repo_dir, got {passed_policy!r}",
        )

    def test_relative_policy_escaping_repo_root_returns_error(self):
        """Mirrors the project_manifest containment rule: a relative
        ``policy`` path with ``..`` segments would land outside the
        repo, and the relative-to-repo semantic is then ambiguous
        (typo vs intentional). Reject and surface a targeted error
        instead of silently reading a file outside the fetched tree.
        Operators who want a shared policy outside the repo use an
        absolute path — see
        ``test_policy_absolute_path_passes_through_unchanged``.
        """
        from sentinel.linting import run_linting
        # Stage a sibling file outside repo_dir to confirm the
        # escape would otherwise reach a real file.
        outside = self.test_dir / "outside_policy.json"
        outside.write_text("{}", encoding="utf-8")
        cfg = self._config(policy="../outside_policy.json")
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "error")
        self.assertIn("policy", result.get("message", ""))
        self.assertIn("outside the repo root", result.get("message", ""))

    @patch("sentinel.linting.subprocess.run")
    def test_policy_absolute_path_passes_through_unchanged(self, mock_run):
        """Absolute ``policy`` paths point at shared / external policy
        files (e.g. ``/etc/sentinel/team.json``); resolving them under
        repo_root would silently corrupt them. Pass through verbatim.
        """
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        shared = self.test_dir / "shared_policy.json"
        shared.write_text("{}", encoding="utf-8")
        cfg = self._config(policy=str(shared))
        result = run_linting(cfg, self._ctx())
        self.assertEqual(result.get("status"), "completed")
        cmd = mock_run.call_args[0][0]
        policy_idx = cmd.index("-policy")
        self.assertEqual(cmd[policy_idx + 1], str(shared))

    @patch("sentinel.linting.subprocess.run")
    def test_subprocess_runs_with_repo_root_as_cwd(self, mock_run):
        """Belt-and-suspenders: set ``cwd=repo_root`` on the
        subprocess so any latent relative-path resolution inside
        aurig-lint (anything Sentinel does not pass explicitly) uses
        the same base as the documented contract.
        """
        from sentinel.linting import run_linting
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_linting(self._config(), self._ctx())
        kwargs = mock_run.call_args.kwargs
        self.assertIn("cwd", kwargs)
        self.assertEqual(
            Path(kwargs["cwd"]).resolve(),
            self.repo_dir.resolve(),
        )

    @patch("sentinel.linting.subprocess.run")
    def test_filenotfounderror_writes_lint_log_with_diagnostic_summary(self, mock_run):
        """Same contract as the timeout case: a missing ``tclsh`` on
        PATH must leave a lint.log behind that names the executable
        the operator was looking for.
        """
        from sentinel.linting import run_linting
        mock_run.side_effect = FileNotFoundError("tclsh not on PATH")
        result = run_linting(self._config(), self._ctx())
        self.assertEqual(result.get("status"), "failed")
        log_file = result.get("log_file")
        self.assertTrue(log_file, "FileNotFoundError path must return a log_file path")
        self.assertTrue(Path(log_file).is_file())
        log_text = Path(log_file).read_text(encoding="utf-8")
        self.assertIn("EXECUTABLE NOT FOUND", log_text)
        self.assertIn("tclsh", log_text)


class TestLintingSchemaValidation(unittest.TestCase):
    """validate_config() coverage for the new phases.linting subblock."""

    def _validate(self, linting_block):
        from sentinel.config_validator import validate_config, ConfigValidationError
        cfg = {
            "schema_version": "1.0",
            "project": {"name": "demo"},
            "fetch": {"type": "git", "url": "https://example.com/r.git"},
            "project_manifest": "manifest.yaml",
            "phases": {"linting": linting_block},
        }
        try:
            validate_config(cfg)
            return None
        except ConfigValidationError as exc:
            return exc.errors

    def test_accepts_minimal_block(self):
        # `enabled: true` alone is valid: every other field has a
        # runtime default, and aurig_lint_path is checked at runtime via
        # the env-var fallback contract.
        self.assertIsNone(self._validate({"enabled": True}))

    def test_accepts_full_block(self):
        self.assertIsNone(self._validate({
            "enabled": True,
            "aurig_lint_path": "/opt/aurig-lint",
            "tclsh_path": "tclsh",
            "fail_on": "warning",
            "format": "md",
            "output_dir": "lint_output",
            "policy": "lint/lint_user.json",
            "include": r".*\.vhd$",
            "exclude": r".*generated.*",
        }))

    def test_rejects_invalid_fail_on(self):
        errors = self._validate({"enabled": True, "fail_on": "panic"}) or []
        self.assertTrue(any("fail_on" in e for e in errors), errors)

    def test_rejects_invalid_format(self):
        errors = self._validate({"enabled": True, "format": "pdf"}) or []
        self.assertTrue(any("format" in e for e in errors), errors)

    def test_rejects_empty_aurig_lint_path(self):
        errors = self._validate({"enabled": True, "aurig_lint_path": ""}) or []
        self.assertTrue(
            any("aurig_lint_path" in e for e in errors), errors
        )

    def test_rejects_non_string_include_regex(self):
        errors = self._validate({"enabled": True, "include": 42}) or []
        self.assertTrue(any("include" in e for e in errors), errors)

    def test_rejects_absolute_output_dir(self):
        """phases.linting.output_dir is resolved under <run_dir>/.
        An absolute path would escape the run dir and contaminate
        the artifact bundle.
        """
        # Use a platform-appropriate absolute path so the test passes
        # on both POSIX and Windows.
        abs_path = "C:/var/lint" if os.name == "nt" else "/var/lint"
        errors = self._validate({"enabled": True, "output_dir": abs_path}) or []
        self.assertTrue(
            any("output_dir" in e and "relative" in e for e in errors),
            errors,
        )

    def test_rejects_output_dir_with_dotdot(self):
        """``..`` segments in output_dir would let the phase write
        outside the run directory.
        """
        errors = self._validate({"enabled": True, "output_dir": "../escape"}) or []
        self.assertTrue(
            any("output_dir" in e and ".." in e for e in errors),
            errors,
        )

    def test_rejects_dot_output_dir(self):
        """``output_dir: "."`` normalizes to ``Path(".")`` whose
        ``.parts`` is empty, which would resolve under
        ``RunContext.output_dir`` to the run directory itself. The
        validator rejects it so ``package_artifacts`` never tries to
        ``copytree`` the run dir into a subdir of itself.
        """
        errors = self._validate({"enabled": True, "output_dir": "."}) or []
        self.assertTrue(
            any("output_dir" in e for e in errors),
            errors,
        )

    def test_rejects_dotslash_output_dir(self):
        """``"./"`` and ``"./."`` normalize to the same empty-parts
        path as ``"."``. Pinned separately so a future change to the
        validator (e.g. stripping leading ``./``) doesn't silently
        re-open the gap.
        """
        for variant in ("./", "./."):
            errors = self._validate({"enabled": True, "output_dir": variant}) or []
            self.assertTrue(
                any("output_dir" in e for e in errors),
                f"variant {variant!r} should have been rejected; errors={errors}",
            )


# ---------------------------------------------------------------------------
# Regression / VUnit backend (sys.executable, _vunit_is_importable contract)
# ---------------------------------------------------------------------------

class TestRegressionVunitBackend(unittest.TestCase):
    """OP-010 (sys.executable) and OP-014 (_vunit_is_importable contract)."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()
        self.repo_dir = self.test_dir / "repo"
        self.repo_dir.mkdir()
        # A real VUnit-style script the backend will pretend to invoke.
        self.vunit_script = self.repo_dir / "run_vunit.py"
        self.vunit_script.write_text(
            "# stand-in for the user's vunit runner\n", encoding="utf-8",
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _ctx(self):
        from sentinel import RunContext
        ctx = RunContext.for_run(self.run_dir)
        ctx.repo_path = self.repo_dir
        return ctx

    def test_no_vunit_symbol_at_module_scope(self):
        """OP-014: the misleading ``VUnit`` class binding stays gone.

        A future "let's add it back to type-annotate something" patch
        would silently re-introduce the suggestion that Sentinel
        drives VUnit via its Python API; this assertion fails loud
        if that happens.
        """
        from sentinel import regression_testing
        self.assertFalse(hasattr(regression_testing, "VUnit"))

    def test_vunit_helper_reports_false_when_import_raises(self):
        """A broken/partial vunit-hdl install (or a stray local
        ``vunit.py`` whose import side-effects blow up) must NOT
        be reported as available — otherwise ``_run_vunit_backend``
        would proceed to subprocess the user's runner script and
        fail with a much less targeted error.
        """
        from unittest.mock import patch
        from sentinel.regression_testing import _vunit_is_importable

        with patch(
            "sentinel.regression_testing.importlib.import_module",
            side_effect=ImportError("vunit broke"),
        ):
            self.assertFalse(_vunit_is_importable())

    def test_vunit_helper_reports_false_when_vunit_attr_missing(self):
        """A package named ``vunit`` that doesn't expose the ``VUnit``
        class (a stray local vunit.py on PYTHONPATH, a vendored
        partial copy, etc.) must also be rejected.
        """
        from types import SimpleNamespace
        from unittest.mock import patch
        from sentinel.regression_testing import _vunit_is_importable

        not_real_vunit = SimpleNamespace()  # no VUnit attribute
        with patch(
            "sentinel.regression_testing.importlib.import_module",
            return_value=not_real_vunit,
        ):
            self.assertFalse(_vunit_is_importable())

    def test_vunit_helper_reports_true_when_import_and_attr_succeed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sentinel.regression_testing import _vunit_is_importable

        real_vunit = SimpleNamespace(VUnit=object)
        with patch(
            "sentinel.regression_testing.importlib.import_module",
            return_value=real_vunit,
        ):
            self.assertTrue(_vunit_is_importable())

    def test_vunit_no_fetched_repo_surfaces_targeted_error(self):
        """OP-034: same shape as the synthesis variant. With
        ``ctx.repo_path`` unset (fetch disabled or failed), the VUnit
        backend must report "no repository available" rather than the
        generic "VUnit script not found" the script-resolution branch
        would produce against the empty <run_dir>/repos placeholder.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        ctx = RunContext.for_run(self.run_dir)
        # Skip ctx.repo_path = ... so it stays None.

        with patch("sentinel.regression_testing.HAS_VUNIT", True):
            result = regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                        }
                    }
                },
                ctx,
            )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("No repository available", result.get("message", ""))

    def test_vunit_failed_fetch_residue_surfaces_targeted_error(self):
        """OP-034 guard precision: a populated ``repos_root`` is only
        treated as pre-stage when fetch was NOT attempted. If
        ``ctx.fetch_attempted`` is True but ``ctx.repo_path`` is still
        None, the content is leftover partial residue from a failed
        local fetch (e.g. copytree raising mid-loop under
        ``continue_on_error``) — exactly the case OP-034 was meant
        to surface. The guard must short-circuit with the targeted
        error rather than fall through to the per-backend
        "VUnit script not found" against an unreliable tree.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        # Simulate the residue a partially-completed local fetch
        # would leave: <repos_root>/<repo_name>/<partial files>.
        residue_dir = self.run_dir / "repos" / "partial_repo"
        residue_dir.mkdir(parents=True)
        (residue_dir / "half_copied.vhd").write_text("-- partial\n", encoding="utf-8")

        ctx = RunContext.for_run(self.run_dir)
        ctx.fetch_attempted = True  # fetch ran, failed before setting repo_path

        with patch("sentinel.regression_testing.HAS_VUNIT", True):
            result = regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                        }
                    }
                },
                ctx,
            )

        # Diagnostic split: failed-fetch state gets its own message,
        # not the generic "enable the fetch phase" wording (which
        # would be misleading — fetch already ran).
        self.assertEqual(result.get("status"), "error")
        self.assertIn(
            "Fetch phase ran but produced no usable repository",
            result.get("message", ""),
        )

    def test_vunit_guard_handles_non_directory_repos_root(self):
        """OP-034 guard robustness: if something has planted a *file*
        at ``<run_dir>/repos`` instead of a directory, the guard must
        still return the targeted error dict — not crash with
        ``NotADirectoryError`` from ``iterdir()``. ``is_dir()`` returns
        False for both missing and non-directory paths, short-circuiting
        before any iteration happens.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        # Plant a regular file where the placeholder dir is expected.
        repos_path = self.run_dir / "repos"
        repos_path.write_text("not a directory\n", encoding="utf-8")

        ctx = RunContext.for_run(self.run_dir)

        with patch("sentinel.regression_testing.HAS_VUNIT", True):
            result = regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                        }
                    }
                },
                ctx,
            )

        self.assertEqual(result.get("status"), "error")
        self.assertIn("No repository available", result.get("message", ""))

    def test_convention_no_fetched_repo_surfaces_targeted_error(self):
        """OP-034 for the convention backend. Without the early
        check, the runner would have produced a "Testbench directory
        not found" error pointing at the empty placeholder — useless
        for diagnosing that fetch never ran.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        ctx = RunContext.for_run(self.run_dir)

        result = regression_testing_phase(
            {
                "phases": {
                    "regression": {
                        "enabled": True,
                        "backend": "convention",
                        "simulator": "ghdl",
                        "testbench_dir": "tb",
                    }
                }
            },
            ctx,
        )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("No repository available", result.get("message", ""))

    def test_vunit_pre_staged_repos_root_skips_targeted_guard(self):
        """OP-034 fallback contract (VUnit variant): pre-staged content
        under ``ctx.repos_root`` must let the run proceed past the
        targeted "no repository available" guard. Whatever the backend
        does next (subprocess, simulator, etc.) is out of scope —
        what matters is that the guard does NOT short-circuit.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        # Pre-stage the VUnit script under repos_root, with repo_path
        # left unset. ctx.repo_root falls back to repos_root, where
        # the backend's script_path lookup will find run_vunit.py.
        repos_root = self.run_dir / "repos"
        repos_root.mkdir(parents=True)
        (repos_root / "run_vunit.py").write_text(
            "# pre-staged vunit runner\n", encoding="utf-8"
        )

        ctx = RunContext.for_run(self.run_dir)  # repo_path left None

        with patch("sentinel.regression_testing.HAS_VUNIT", True), patch(
            "sentinel.regression_testing.subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                        }
                    }
                },
                ctx,
            )

        self.assertNotIn("No repository available", result.get("message", "") or "")
        mock_run.assert_called_once()

    def test_convention_pre_staged_repos_root_skips_targeted_guard(self):
        """OP-034 fallback contract (convention variant). Staging the
        testbench dir under ``ctx.repos_root`` is enough — the run
        must reach the existing testbench-directory check rather than
        being aborted by the targeted guard.
        """
        from sentinel import RunContext
        from sentinel.regression_testing import regression_testing_phase

        # repos_root/tb exists → guard sees a populated placeholder
        # and steps aside; the backend then proceeds to its own
        # per-simulator checks (irrelevant to this contract).
        tb_dir = self.run_dir / "repos" / "tb"
        tb_dir.mkdir(parents=True)

        ctx = RunContext.for_run(self.run_dir)  # repo_path left None

        result = regression_testing_phase(
            {
                "phases": {
                    "regression": {
                        "enabled": True,
                        "backend": "convention",
                        "simulator": "ghdl",
                        "testbench_dir": "tb",
                    }
                }
            },
            ctx,
        )
        self.assertNotIn("No repository available", result.get("message", "") or "")

    def test_vunit_helper_reports_false_when_attr_lookup_raises(self):
        """A PEP 562 module can define ``__getattr__`` that raises
        anything. ``hasattr`` would propagate non-AttributeError
        exceptions and crash module-load (since HAS_VUNIT is computed
        at import time); the helper uses ``getattr(..., None)`` inside
        a broad ``except Exception`` so any attribute-lookup failure
        collapses to ``False`` instead.
        """
        from unittest.mock import patch
        from sentinel.regression_testing import _vunit_is_importable

        class HostileModule:
            def __getattr__(self, name):  # noqa: D401 - test stub
                raise RuntimeError(f"hostile __getattr__ refusing {name!r}")

        with patch(
            "sentinel.regression_testing.importlib.import_module",
            return_value=HostileModule(),
        ):
            self.assertFalse(_vunit_is_importable())

    def test_vunit_disabled_returns_targeted_error(self):
        """The HAS_VUNIT gate still works with the current
        ``_vunit_is_importable()`` availability check: with VUnit
        absent, the backend returns the same error dict rather than
        attempting to shell out.
        """
        from sentinel.regression_testing import regression_testing_phase

        with patch("sentinel.regression_testing.HAS_VUNIT", False):
            result = regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                        }
                    }
                },
                self._ctx(),
            )
        self.assertEqual(result.get("status"), "error")
        self.assertIn("VUnit", result.get("message", ""))

    @patch("sentinel.regression_testing.subprocess.run")
    def test_vunit_command_uses_sys_executable_not_literal_python(self, mock_run):
        """OP-010: VUnit subprocess argv[0] must be sys.executable so
        it picks up the venv's packages (including vunit-hdl itself)
        on hosts where Python is only exposed as `python3` or where
        `python` is not on PATH at all.
        """
        from sentinel.regression_testing import regression_testing_phase

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("sentinel.regression_testing.HAS_VUNIT", True):
            regression_testing_phase(
                {
                    "phases": {
                        "regression": {
                            "enabled": True,
                            "backend": "vunit",
                            "vunit_run_script": "run_vunit.py",
                            "simulator": "ghdl",
                        }
                    }
                },
                self._ctx(),
            )

        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("run_vunit.py"))


# ---------------------------------------------------------------------------
# Pre-run phase (timeout, enabled-but-empty rejection)
# ---------------------------------------------------------------------------

class TestPreRunPhase(unittest.TestCase):
    """project_setup() + pre_run validator (OP-007 timeout, OP-023 trap)."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _ctx(self):
        from sentinel import RunContext
        return RunContext.for_run(self.run_dir)

    @patch("sentinel.project_setup.subprocess.run")
    def test_program_timeout_propagates_as_timeoutexpired(self, mock_run):
        """A hung pre_run.program must raise subprocess.TimeoutExpired
        rather than wait forever; main.execute_phases catches the
        exception and converts it into a phase failure dict.
        """
        from sentinel.project_setup import project_setup

        mock_run.side_effect = subprocess.TimeoutExpired("hung-cmd", 5)

        config = {
            "pre_run": {
                "enabled": True,
                "program": "hung-cmd",
                "timeout_seconds": 5,
            }
        }
        with self.assertRaises(subprocess.TimeoutExpired):
            project_setup(config, self._ctx())

        # The timeout we configured was actually plumbed through.
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs.get("timeout"), 5)

    @patch("sentinel.project_setup.subprocess.run")
    def test_default_timeout_used_when_field_omitted(self, mock_run):
        from sentinel.project_setup import project_setup
        from sentinel.project_setup import DEFAULT_PRE_RUN_TIMEOUT_SECONDS

        mock_run.return_value = MagicMock(returncode=0)

        config = {
            "pre_run": {
                "enabled": True,
                "program": "/bin/true",
            }
        }
        project_setup(config, self._ctx())
        self.assertEqual(
            mock_run.call_args.kwargs.get("timeout"),
            DEFAULT_PRE_RUN_TIMEOUT_SECONDS,
        )

    def test_validator_rejects_enabled_with_empty_scripts_and_no_program(self):
        """OP-023: enabled=true with neither scripts nor program is a
        silent no-op trap; the validator must reject it.
        """
        from sentinel.config_validator import (
            ConfigValidationError, validate_config,
        )

        config = {
            "schema_version": "1.0",
            "project": {"name": "alpha"},
            "fetch": {"type": "git", "url": "https://example/x.git"},
            "project_manifest": "m.txt",
            "pre_run": {"enabled": True},  # no scripts, no program
            "phases": {"synthesis": {"enabled": True}},
        }
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(config)
        joined = "\n".join(ctx.exception.errors)
        self.assertIn("pre_run.enabled is true", joined)
        self.assertIn("scripts", joined)
        self.assertIn("program", joined)

    def test_validator_accepts_enabled_with_only_scripts(self):
        from sentinel.config_validator import validate_config

        config = {
            "schema_version": "1.0",
            "project": {"name": "alpha"},
            "fetch": {"type": "git", "url": "https://example/x.git"},
            "project_manifest": "m.txt",
            "pre_run": {"enabled": True, "scripts": ["setup.sh"]},
            "phases": {"synthesis": {"enabled": True}},
        }
        validate_config(config)  # must not raise

    def test_validator_accepts_enabled_with_only_program(self):
        from sentinel.config_validator import validate_config

        config = {
            "schema_version": "1.0",
            "project": {"name": "alpha"},
            "fetch": {"type": "git", "url": "https://example/x.git"},
            "project_manifest": "m.txt",
            "pre_run": {"enabled": True, "program": "/bin/setup"},
            "phases": {"synthesis": {"enabled": True}},
        }
        validate_config(config)  # must not raise

    def test_validator_accepts_disabled_with_empty_block(self):
        """A disabled pre_run with no fields is the common explicit
        opt-out and must NOT trigger the OP-023 rule.
        """
        from sentinel.config_validator import validate_config

        config = {
            "schema_version": "1.0",
            "project": {"name": "alpha"},
            "fetch": {"type": "git", "url": "https://example/x.git"},
            "project_manifest": "m.txt",
            "pre_run": {"enabled": False},
            "phases": {"synthesis": {"enabled": True}},
        }
        validate_config(config)

    def test_all_configured_scripts_missing_raises(self):
        """Runtime version of the OP-023 trap: the validator only sees
        the schema (scripts list non-empty) and can't tell that every
        path will be missing on disk. If 0 of N configured scripts
        exist, project_setup raises so main converts it into a
        phase failure that monitoring can alarm on.
        """
        from sentinel.project_setup import project_setup

        # Two scripts listed, neither created on disk.
        config = {
            "pre_run": {
                "enabled": True,
                "scripts": ["does/not/exist1.sh", "does/not/exist2.sh"],
            }
        }
        with self.assertRaisesRegex(RuntimeError, "none existed on disk"):
            project_setup(config, self._ctx())

    @patch("sentinel.project_setup.subprocess.run")
    def test_partial_script_run_logs_count(self, mock_run):
        """A mix of present + missing scripts must NOT raise: the
        present ones did real work. The success log surfaces ran/
        configured so the operator can see the gap at a glance.
        """
        from sentinel.project_setup import project_setup

        mock_run.return_value = MagicMock(returncode=0)

        # Stage one real script under the run_dir; reference one missing
        # alongside it so configured=2, ran=1.
        real = self.run_dir / "real.sh"
        real.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

        config = {
            "pre_run": {
                "enabled": True,
                "scripts": ["real.sh", "missing.sh"],
            }
        }
        # Capture log output to verify the new "N/M scripts ran" wording.
        with self.assertLogs("sentinel.project_setup", level="INFO") as cap:
            project_setup(config, self._ctx())
        joined = "\n".join(cap.output)
        self.assertIn("1/2 scripts ran", joined)
        self.assertIn("no program configured", joined)
        # subprocess was called once (only the real script).
        self.assertEqual(mock_run.call_count, 1)

    def test_validator_rejects_non_positive_timeout(self):
        from sentinel.config_validator import (
            ConfigValidationError, validate_config,
        )

        for bad_value in (0, -1, True, "1800", 1.5):
            config = {
                "schema_version": "1.0",
                "project": {"name": "alpha"},
                "fetch": {"type": "git", "url": "https://example/x.git"},
                "project_manifest": "m.txt",
                "pre_run": {
                    "enabled": True,
                    "program": "/bin/true",
                    "timeout_seconds": bad_value,
                },
                "phases": {"synthesis": {"enabled": True}},
            }
            with self.assertRaises(ConfigValidationError) as ctx:
                validate_config(config)
            self.assertTrue(
                any("timeout_seconds" in e for e in ctx.exception.errors),
                f"timeout_seconds={bad_value!r} should be rejected, "
                f"got: {ctx.exception.errors}",
            )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):
    """Edge cases: failed clones, simulator timeouts, etc."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.run_dir = self.test_dir / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("sentinel.fetch_code.HAS_GITPYTHON", new=False)
    @patch("sentinel.fetch_code.subprocess.run")
    def test_git_clone_failure_returns_none(self, mock_subprocess):
        from sentinel import RunContext
        from sentinel.fetch_code import fetch_code

        mock_subprocess.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: repository not found"
        )
        cfg = {
            "fetch": {"type": "git", "url": "https://github.com/invalid/repo.git", "branch": "main"},
        }
        self.assertIsNone(fetch_code(cfg, RunContext.for_run(self.run_dir)))


# ---------------------------------------------------------------------------
# OP-035: failure-status aggregation in main.execute_phases
# ---------------------------------------------------------------------------

class TestFailedPhaseAggregation(unittest.TestCase):
    """The end-of-run filter in ``main.execute_phases`` historically
    only matched the literal uppercase ``"FAILED"`` produced by its
    own exception handlers, while every in-phase early-return failure
    path (``regression_testing`` and ``synthesis``, ~22 sites total)
    returns lowercase ``"failed"`` or ``"error"``. The new
    ``_is_failure_status`` helper canonicalises all three. These
    tests pin the contract directly on the helper so the
    case-insensitive recognition can't regress under a future
    refactor.
    """

    def test_uppercase_FAILED_counts_as_failure(self):
        """Regression-protect: the original ``"FAILED"`` (uppercase)
        that this module's own exception handlers produce must still
        be recognised, otherwise we'd silently flip the prior
        behavior for exception-driven phase failures.
        """
        from sentinel.main import _is_failure_status
        self.assertTrue(_is_failure_status("FAILED"))

    def test_lowercase_failed_counts_as_failure(self):
        """``"failed"`` is what ``_run_vunit_backend`` returns on
        subprocess timeout / non-zero exit, and what
        ``_run_vivado_synthesis_custom`` returns when the vendor
        binary fails. Without OP-035 these vanished from the overall
        run status.
        """
        from sentinel.main import _is_failure_status
        self.assertTrue(_is_failure_status("failed"))

    def test_lowercase_error_counts_as_failure(self):
        """``"error"`` is the dominant marker for in-phase
        configuration/validation early returns ("VUnit not
        installed", "synthesis_tool_path missing", the OP-034
        no-repository guards, etc.). Recognising this is the central
        OP-035 fix.
        """
        from sentinel.main import _is_failure_status
        self.assertTrue(_is_failure_status("error"))

    def test_mixed_case_variants_count_as_failure(self):
        """The helper is case-insensitive, so a future caller that
        writes ``"Failed"`` / ``"Error"`` / ``"ERROR"`` won't quietly
        slip past the aggregation.
        """
        from sentinel.main import _is_failure_status
        for variant in ("Failed", "Error", "ERROR", "fAiLeD"):
            with self.subTest(status=variant):
                self.assertTrue(_is_failure_status(variant))

    def test_success_and_neutral_statuses_not_counted(self):
        """Whatever the executor emits for non-failure phases
        (``"OK"`` / ``"SKIPPED"`` / ``"PENDING"`` / ``"UNKNOWN"``)
        and whatever phases emit on success (``"completed"``) must
        NOT be counted as failure.
        """
        from sentinel.main import _is_failure_status
        for status in ("OK", "SKIPPED", "PENDING", "UNKNOWN", "completed"):
            with self.subTest(status=status):
                self.assertFalse(_is_failure_status(status))

    def test_non_string_and_missing_status_not_counted(self):
        """``r.get("status")`` returns ``None`` for malformed phase
        results — that must be a non-failure (the executor logs it
        as ``"UNKNOWN"`` separately) rather than triggering
        ``AttributeError`` on ``.lower()``.
        """
        from sentinel.main import _is_failure_status
        for value in (None, 0, False, ["failed"], {"status": "failed"}):
            with self.subTest(value=value):
                self.assertFalse(_is_failure_status(value))

    def test_aggregation_picks_up_lowercase_error_phase(self):
        """End-to-end on the aggregation site: a synthesised
        ``phase_results`` dict containing one ``"error"`` phase and
        one successful phase must yield a single failed phase in the
        final list. Before OP-035 the ``"error"`` phase was
        silently dropped.
        """
        from sentinel.main import _is_failure_status

        phase_results = {
            "fetch": {"status": "OK"},
            "regression": {"status": "error", "message": "No repository available"},
            "synthesis": {"status": "completed"},
        }
        failed = [n for n, r in phase_results.items()
                  if _is_failure_status(r.get("status"))]
        self.assertEqual(failed, ["regression"])

    def test_abort_if_failed_raises_on_failure_status_when_strict(self):
        """``continue_on_error=False`` is documented as "first phase
        failure aborts". Without OP-035's per-phase abort, a phase
        that returned ``{"status": "error"}`` (rather than raising)
        bypassed that contract — downstream phases ran anyway.
        """
        from sentinel.main import _abort_if_failed

        for status in ("error", "failed", "FAILED", "Error"):
            with self.subTest(status=status):
                with self.assertRaises(RuntimeError) as ctx:
                    _abort_if_failed(
                        "regression", status, continue_on_error=False,
                        detail="No repository available",
                    )
                # Detail propagates into the raised message so logs /
                # exception chain carry the actual cause, not just
                # the phase name.
                self.assertIn("regression", str(ctx.exception))
                self.assertIn("No repository available", str(ctx.exception))

    def test_abort_if_failed_silent_when_continue_on_error(self):
        """Default ``continue_on_error=True`` runs must NOT raise on
        a structured failure — the failure is captured in
        ``phase_results`` and surfaced through the end-of-run
        aggregation, but the run keeps going so partial artifacts
        from later phases can still be collected.
        """
        from sentinel.main import _abort_if_failed

        for status in ("error", "failed", "FAILED"):
            with self.subTest(status=status):
                # Must not raise.
                _abort_if_failed(
                    "regression", status, continue_on_error=True,
                    detail="anything",
                )

    def test_abort_if_failed_silent_on_success_status(self):
        """Even with ``continue_on_error=False``, a successful phase
        (or a neutral marker like ``"SKIPPED"``) must not trigger
        the abort.
        """
        from sentinel.main import _abort_if_failed

        for status in ("OK", "completed", "SKIPPED", "PENDING", "UNKNOWN", None):
            with self.subTest(status=status):
                _abort_if_failed(
                    "regression", status, continue_on_error=False,
                    detail="",
                )

    def test_abort_if_failed_omits_detail_suffix_when_empty(self):
        """When the phase result carries no message/error field, the
        raised RuntimeError should still be readable — no stray
        trailing ": " or empty parens.
        """
        from sentinel.main import _abort_if_failed

        with self.assertRaises(RuntimeError) as ctx:
            _abort_if_failed(
                "synthesis", "failed", continue_on_error=False, detail="",
            )
        msg = str(ctx.exception)
        self.assertEqual(msg, "synthesis phase failed")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions and helpers."""

    def test_handle_remove_readonly(self):
        from sentinel.fetch_code import handle_remove_readonly

        test_dir = tempfile.mkdtemp()
        try:
            test_file = os.path.join(test_dir, "readonly.txt")
            with open(test_file, "w") as f:
                f.write("test")

            os.chmod(test_file, 0o444)
            handle_remove_readonly(os.remove, test_file, None)

            self.assertFalse(os.path.exists(test_file))
        finally:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir, ignore_errors=True)

    def test_safe_rmtree_removes_readonly_tree(self):
        """Smoke test: verifies _safe_rmtree selects a valid keyword (onexc/onerror)
        for the current Python version and removes the tree. On POSIX the readonly
        file is unlinked via the writable parent directory without invoking the
        callback — Windows-specific callback behavior is covered by
        test_handle_remove_readonly.
        """
        from sentinel.fetch_code import _safe_rmtree

        test_dir = tempfile.mkdtemp()
        try:
            nested = os.path.join(test_dir, "repo")
            os.makedirs(nested)
            readonly_file = os.path.join(nested, "readonly.txt")
            with open(readonly_file, "w") as f:
                f.write("test")
            os.chmod(readonly_file, 0o444)

            _safe_rmtree(test_dir)

            self.assertFalse(os.path.exists(test_dir))
        finally:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
