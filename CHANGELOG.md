<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor S.r.l. -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `documentation` phase backed by aurig-doc's project document runner
  (subprocess). Exit contract 0/2: `completed`/`error` only, no
  quality-failure state; `continue_on_error` honored. (#6)

### Changed

- **BREAKING:** config key `phases.linting.tcl4fpga_path` renamed to
  `aurig_lint_path` (env `SENTINEL_TCL4FPGA_PATH` renamed to
  `SENTINEL_AURIG_LINT_PATH`); `fail_on` value `note` renamed to
  `info`. Migration: rename the key/env var in existing configs and
  replace `fail_on: note` with `fail_on: info`. (#1, #3, #4)
- pre-commit EOL hooks exclude `.bat`/`.ps1` to match
  `.gitattributes`. (#2)

## [0.1.0] - 2026-06-10

### Added

- Initial release of AURIG Sentinel.
- Multi-config orchestration for nightly FPGA build pipelines.
- Phase-based pipeline (fetch, pre_run, linting, regression,
  synthesis) with per-phase configuration.
- Git and local source fetch.
- Regression backend supporting GHDL, ModelSim/Questa, Active-HDL, xsim.
- Synthesis support for Vivado (Quartus is stubbed).
- Time-window gating, artifact bundling, retention-based cleanup.
- systemd + Windows Task Scheduler deployment scaffolding.
