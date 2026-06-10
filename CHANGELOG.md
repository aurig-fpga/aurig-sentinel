<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor S.r.l. -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
