# Sentinel

> Nightly orchestrator for FPGA build pipelines: one YAML per project, every
> phase delegated to the right tool, every run reproducible.

Sentinel is a Python runner that schedules and executes FPGA project
pipelines defined as YAML files. It is built for FPGA teams that need
reproducible nightly regression, weekly synthesis runs, and consistent
artifact bundling without writing bespoke shell glue per project.
Sentinel itself is intentionally narrow: it does not parse VHDL, it does
not own the project structure, and it does not impose a build system. It
fetches code, gates execution on a time window, drives a small set of
named phases, captures logs, and zips artifacts. The HDL-aware work is
delegated to `aurig-core` and to vendor EDA tools that the project's own
manifest already knows how to drive.

---

## What It Does

A typical deployment of Sentinel sits on a workstation that owns the
licensed EDA toolchain (Vivado, ModelSim/Questa, GHDL, Active-HDL) and
runs unattended overnight. A small set of YAML files under `configs/`
describes what to do for each project. Sentinel reads the files,
validates them, and for each one fetches the latest code, optionally
runs setup hooks, executes the enabled phases, and writes a timestamped
run directory under `<output.base_dir>/<project>/`.

The intended scenarios are concrete:

- **Nightly regression** — across N projects, fetch the current `main`
  branch, run the convention or VUnit testbench backend, and produce a
  per-project log + summary that a human reads the next morning. The
  `night_time_window` setting keeps the workstation free during the day
  and lets the timer fire only between 22:00 and 06:00.
- **Weekly synthesis sweeps** — ahead of a release, every project runs
  its own `scripts/run_synthesis.tcl` against Vivado in batch mode.
  Sentinel collects the resulting `.rpt` files, archives them as
  `sentinel_artifacts.zip`, and the team compares utilisation/timing
  trends week over week.
- **Pre-flight on PRs** — drive Sentinel manually with `--config <one>`
  to reproduce the nightly environment before merging a change.

Sentinel is not a CI system replacement. It is a deterministic,
filesystem-driven orchestrator for runs that require licensed EDA
toolchains and live close to the silicon.

## How It Fits In

Sentinel is one layer in a stack:

```
+----------------------------------------------------------+
|  Sentinel                                                |
|  - Discovers per-project YAML configs                    |
|  - Gates on schema, time window, and enabled phases      |
|  - Drives fetch / pre_run / regression / synthesis       |
|  - Bundles logs and artifacts per run                    |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|  aurig-core (project manifest engine)                    |
|  - Owns the canonical project YAML inside each repo      |
|  - Translates manifest into vendor TCL scripts           |
|  - Backs the linting phase; documentation up next        |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|  EDA tools (system prerequisites)                        |
|  - Vivado / Quartus (synthesis)                          |
|  - ModelSim / Questa / GHDL / Active-HDL / xsim          |
|  - Git, Python                                           |
+----------------------------------------------------------+
```

Sentinel does not understand VHDL or Verilog. The repository under test
is expected to ship its own canonical project manifest — see
aurig-core. Sentinel resolves the manifest path
(`project_manifest`) inside the fetched repo and hands it off; the
vendor-specific TCL scripts the manifest produces are what actually
build the design.

## Why Sentinel

Sentinel solves one problem narrowly: scheduling FPGA pipelines on the
workstation that owns the licensed EDA toolchain. The trade is breadth
for low maintenance cost and zero outbound coupling — characteristics
that matter to small FPGA teams and to organisations whose IP cannot
leave the lab network.

### Where it fits

**Use Sentinel when:**

- Nightly regression and weekly synthesis run on **one workstation**
  (or a handful, one per site).
- The toolchain is **licensed and node-locked** (Vivado, Quartus,
  ModelSim/Questa, Active-HDL) and cannot move to cloud runners.
- The IP/HDL **must stay on the local network** (defence, medical,
  aerospace, regulated industries).
- The team is **1–5 people** with no dedicated CI administrator.
- You want **YAML in git**, not click-paths in a web UI.

**Look elsewhere when:**

- PR-gating is the primary need — Sentinel is pull-based on a timer,
  not webhook-driven.
- You need **distributed multi-machine** builds with central
  scheduling.
- You need a **multi-user web UI**, build history search, or RBAC.
- The pipeline is software-only and can run on cloud-hosted runners.

### Compared to GitHub Actions / GitLab CI

|                                          | Sentinel                                       | GHA / GitLab CI                                          |
|------------------------------------------|------------------------------------------------|----------------------------------------------------------|
| EDA license on the runner                | Native — runs on the licensed workstation      | Self-hosted runner only; cloud-hosted excluded           |
| Air-gapped / no outbound channel         | Yes (`fetch.type: local` removes git too)      | No (cloud) or partial (self-hosted agent calls home)     |
| Time-of-day gating                       | Built in (`night_time_window`, cross-midnight) | `schedule:` cron; no time-window model                   |
| Multi-project batch on one box           | Native (walks N YAMLs, skip-and-continue)      | Per-repo trigger; cross-repo batches need orchestration  |
| PR-gate / status checks                  | No                                             | Yes (native)                                             |
| Marketplace / actions ecosystem          | None                                           | Large                                                    |
| Web UI / build history                   | Logs + summary text                            | Full UI, history retained                                |

GHA and GitLab CI are **complementary, not competing**: they own the
firmware/PR side, Sentinel owns the on-prem nightly that licensed EDA
tools force into the lab. Even with self-hosted runners, GHA/GitLab CI
still require an outbound agent that calls home and still need to be
administered alongside the EDA workstation itself.

### Compared to Jenkins (self-hosted)

Jenkins is the closest direct competitor and is more capable in several
dimensions — we say so plainly.

|                                          | Sentinel                                  | Jenkins                                              |
|------------------------------------------|-------------------------------------------|------------------------------------------------------|
| Setup cost                               | `pip install` + a YAML per project        | JVM service + plugin set + admin user                |
| Always-on service                        | No — timer-fired, exits after each run    | Yes — port 8080, persistent JVM                      |
| Plugin / extension surface               | None                                      | Large — plugin drift, CVEs, LTS migrations           |
| Config source of truth                   | YAML in git                               | Web UI by default; Jenkinsfile/JobDSL as opt-in      |
| Time-window gating                       | Built in                                  | cron + quiet-period + custom logic                   |
| Skip-and-continue across N projects      | Native                                    | Pipeline-of-pipelines or matrix builds               |
| Multi-machine distributed builds         | No                                        | Yes (master/agent)                                   |
| Web UI, build history, RBAC              | No                                        | Yes                                                  |
| PR-gate via webhook                      | No                                        | Yes (plugins)                                        |
| Notifications                            | None today                                | Built-in / plugin                                    |

**Pick Jenkins** when the org already runs it, needs distributed agents,
needs PR gating, has a Jenkins admin in the team, or has more than
~5 engineers on the same pipeline.

**Pick Sentinel** when the alternative is "bash + cron + sticky notes"
or "a Jenkins nobody is maintaining anymore". Sentinel is what you
reach for when a full CI server is too much surface area for the 5% of
features you would actually use.

### The Sentinel + aurig-core bundle

The dimension that does not show up in feature checkboxes is **what
ships pre-integrated**. Sentinel and aurig-core share a designed
boundary: aurig-core owns the canonical project manifest (sources,
libraries, top-level, simulation metadata); Sentinel hands that
manifest off to the right phase.

The AURIG stack ships, **as a single offer**:

- HDL **linting** as a Sentinel phase backed by aurig-core (active —
  aurig-core's project lint runner driven as a subprocess).
- HDL **documentation** generation as a Sentinel phase backed by
  aurig-core (in progress).
- Vendor-agnostic **synthesis script generation** through the manifest,
  not duplicated per project.
- **Regression**, **synthesis**, and **artifact bundling** in one
  nightly batch.

The same capabilities can be assembled on Jenkins — on a large team
that may even be the right call. The difference is **who does the
assembly**:

|                              | Sentinel + aurig-core                              | Jenkins + your linter + your doc generator   |
|------------------------------|----------------------------------------------------|----------------------------------------------|
| Linter integration           | Phase contract; backend ships with the bundle      | Plugin or freestyle step you write           |
| Documentation integration    | Phase contract; backend ships with the bundle      | Plugin or freestyle step you write           |
| Manifest-driven sources      | Native — aurig-core reads the project manifest     | You write the discovery in groovy / shell    |
| Vendor TCL generation        | Native — aurig-core produces it                    | Hand-maintained per project                  |
| Upgrades                     | One `pip install -U` for both layers               | Plugin-by-plugin compatibility matrix        |

For a lab that wants the FPGA-aware nightly **without writing the
integration themselves**, that is the proposition: Sentinel is the
orchestrator, aurig-core is the HDL brain, and they are designed to
compose. The `linting` backend ships today; the `documentation`
backend lands through the items tracked under
[Roadmap → In progress](#in-progress).

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/aurig-fpga/aurig-sentinel.git
cd aurig-sentinel
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e .

# 2. Copy the template and edit it for your project
cp configs/example.yaml configs/my_project.yaml
$EDITOR configs/my_project.yaml

# 3. Validate without executing
python run.py --config configs/my_project.yaml --dry-run

# 4. Run for real (single project)
python run.py --config configs/my_project.yaml

# 5. Or run every config in configs/ in one shot (typical nightly mode)
python run.py
```

A minimal config that actually validates looks like this:

```yaml
schema_version: "1.0"
project:
  name: minimal-demo
fetch:
  type: git
  url: https://github.com/owner/repo.git
project_manifest: manifest/sentinel.yaml
phases:
  synthesis:
    enabled: true
    synthesis_tool: vivado
    synthesis_tool_path: /tools/Xilinx/Vivado/2024.1
    synthesis_script: vivado
    repo_synthesis_script: scripts/run_synthesis.tcl
```

## Installation

### Prerequisites

- **Python 3.9+** with `pip` and `venv`.
- **Git** on `PATH` (used by GitPython, with a subprocess fallback).
- **aurig-core** — required by the `linting` phase (project lint
  runner); will also back `documentation` when that backend lands. See
  [aurig-core](https://github.com/aurig-fpga/aurig-core) (not yet
  published — coming soon).
- **EDA tools as needed**, available on `PATH` for the user that runs
  Sentinel:
  - **Synthesis:** Xilinx Vivado (Quartus is a stub).
  - **Simulation:** ModelSim/Questa, GHDL, Active-HDL (`vsimsa`), or
    Vivado's `xsim`.
- **Project manifest** — each project's repo must contain a YAML file
  conformant to the canonical AURIG project manifest schema. Sentinel
  only resolves the relative path; the manifest format is documented
  in the aurig-core repository.

### Install Sentinel

```bash
# Editable install for development
pip install -e .

# With the optional VUnit regression backend
pip install -e .[regression]
```

Once installed, the entry point `sentinel` is available on `PATH` and is
equivalent to `python run.py`.

## How Sentinel Works

### Per-project YAML configs

Each project gets its own YAML file under a configs directory. Sentinel
processes one file per project per run. The file names are arbitrary —
discovery picks every top-level `*.yaml` — but the `project.name`
declared **inside** each file must be unique across the discovered set,
**regardless of `output.base_dir`**. Two reasons:

- When the configs share a `base_dir` (the common case, since it
  defaults to `./runs`) they would write into the same
  `<base_dir>/<project.name>/` run folder and stomp on each other's
  artifacts.
- Even when their `base_dir` differs, every log line and every entry
  in the per-run summary refers to projects only by name; two
  configs claiming the same name leave monitoring unable to tell
  their runs apart.

Sentinel aborts with exit code `2` before any phase runs and lists the
offending paths. The full schema is documented in [Configuration
Reference](#configuration-reference) below;
[`configs/example.yaml`](configs/example.yaml) is the canonical
commented template.

### The project manifest

The repository being built must contain a YAML file describing its own
HDL structure — sources, libraries, top-level entity, simulation
metadata, and so on — in the aurig-core canonical form. Sentinel reads
the path to this file from `project_manifest` in the per-project
config and passes it to phases that need it. Sentinel performs no
parsing or validation of the manifest's contents; the schema is owned
by aurig-core.

### Configuration discovery

When Sentinel is invoked without `--config` or `--config-dir`, it walks
this priority order:

1. `$SENTINEL_CONFIGS_DIR`, if set and the directory exists.
2. `./configs/` relative to the working directory, if it exists.
3. `~/.sentinel/configs/`, if it exists.
4. Otherwise, exit with an error suggesting the three options above.

The selected directory is scanned for top-level `*.yaml` files,
**alphabetically sorted**. Subdirectories are ignored, including the
conventional `disabled/`. Hidden files (`.*`) and editor swap files
(`*.yaml.swp`) are also skipped. To temporarily disable a project
without deleting its config, move it under `configs/disabled/`.

### Pipeline phases

| Phase           | Description                                  | Status                                            |
|-----------------|----------------------------------------------|---------------------------------------------------|
| `fetch`         | Git clone or local copy into the run dir     | Active                                            |
| `pre_run`       | User-supplied scripts and a main program     | Active                                            |
| `linting`       | VHDL style/quality checks via aurig-core     | Active (`aurig-core` project lint runner)         |
| `documentation` | HDL documentation generation                 | Pending aurig-core integration                    |
| `regression`    | Simulation-based regression                  | Active (`vunit` + `convention` backends)          |
| `synthesis`     | FPGA synthesis via repo-provided TCL         | Active (Vivado; Quartus stub)                     |
| `deployment`    | Bitstream programming                        | Roadmap (schema accepts it, no backend)           |

Phases not listed under `phases:` default to disabled. `documentation`
logs a `PENDING` status with a TODO marker when enabled and does not
fail the pipeline. `deployment` behaves the same way until its backend
lands.

## Usage

### Run a single config

```bash
python run.py --config configs/my_project.yaml
```

Loads, validates, and executes one project. Exit code is `0` on success
or `1` if any phase failed during execution.

### Run all configs in a directory

```bash
python run.py --config-dir configs/
# or, with discovery
python run.py
```

Each YAML in the directory is processed independently. A validation
failure on one file is reported and the runner moves on
(skip-and-continue). The aggregate summary at the end reports how many
configs were ok, skipped (validation), blocked (night-window), or
failed (execution).

### Dry run

```bash
$ python run.py --config configs/example.yaml --dry-run
[dry-run] /path/to/configs/example.yaml: would run project 'example' (phases: synthesis)

1 config(s) processed: 1 ok, 0 skipped (validation), 0 blocked (night-window), 0 failed (execution).
```

`--dry-run` validates every discovered config against the v1.0 schema
and prints which phases would run, without touching the filesystem
beyond reading the YAML.

### Disable a config

Move the file under `configs/disabled/`. Discovery only walks the top
level of the configs directory, so anything inside `disabled/` is
parked until you move it back.

### Nightly automation

Production deployments use systemd timers on Linux and Task Scheduler
on Windows. The scaffolding scripts ship under
[`deployment/`](deployment/):

- [`deployment/systemd/install-system.sh`](deployment/systemd/install-system.sh)
  installs `/opt/sentinel`, a service user, the systemd unit, and the
  timer.
- [`deployment/setup-user.sh`](deployment/setup-user.sh) sets up a
  user-level install for the same user that runs the EDA tools.
- [`deployment/windows/install.ps1`](deployment/windows/install.ps1)
  registers a scheduled task on Windows.

See [`deployment/INSTALLATION_COMPARISON.md`](deployment/INSTALLATION_COMPARISON.md)
for the trade-offs between the two Linux modes.

### Exit codes

| Code | Meaning                                                                                          |
|------|--------------------------------------------------------------------------------------------------|
| `0`  | No execution failures (`failed (execution)` count is zero). Configs may still be skipped (validation) or blocked (night-window); those don't escalate the exit code. |
| `1`  | At least one execution failure (`failed (execution)` count is non-zero). Does not imply any config completed successfully — every attempted run could have failed. |
| `2`  | Misconfiguration: configs directory not found or empty, two configs declare the same `project.name`, CLI flags are inconsistent, or `--config` points at a file that does not exist. No phases ran. |

The empty-directory case yields `2` (not `0`) so cron / Task Scheduler
can alarm on a nightly run that didn't process anything — useful when
all configs end up under `configs/disabled/` or when the configs
directory was inadvertently moved.

## Configuration Reference

The full v1.0 schema. Required fields are flagged; everything else has
the default shown.

### `schema_version` (string, required)

Must be the literal string `"1.0"`. Any other value is rejected with an
explicit error. The field exists so that future breaking schema changes
can be detected at load time.

```yaml
schema_version: "1.0"
```

### `project` (mapping, required)

| Field         | Type   | Required | Default | Notes                                              |
|---------------|--------|----------|---------|----------------------------------------------------|
| `name`        | string | yes      | —       | Non-empty. Used as the run-output subdirectory.    |
| `description` | string | no       | —       | Free-form, surfaces in logs.                       |

```yaml
project:
  name: my-project
  description: Optional one-liner.
```

### `global_settings` (mapping, optional)

The whole block can be omitted; every nested key defaults sensibly.

| Field                | Type    | Default  | Notes                                                |
|----------------------|---------|----------|------------------------------------------------------|
| `night_time_window`  | mapping | unset    | When set, both `start` and `end` (HH:MM) required.   |
| `cleanup`            | mapping | unset    | See below.                                           |
| `continue_on_error`  | bool    | `true`   | When `false`, the first phase failure aborts.        |
| `log_level`          | enum    | `normal` | One of `quiet`, `normal`, `verbose`.                 |

`night_time_window` gates execution on time of day and supports
cross-midnight ranges (e.g. `22:00`–`06:00`). Outside the window, the
config is reported as `blocked` and skipped.

`cleanup` controls automatic pruning of old run directories under the
project's runs root:

| Field            | Type | Default | Notes                                                                |
|------------------|------|---------|----------------------------------------------------------------------|
| `enabled`        | bool | `false` | When `false`, no cleanup runs.                                       |
| `retention_days` | int  | `30`    | Must be `> 0`. Run dirs older than this are removed.                 |

A run directory containing a `.release` or `.keep` marker file is
exempt from cleanup.

```yaml
global_settings:
  night_time_window:
    start: "22:00"
    end:   "06:00"
  cleanup:
    enabled: true
    retention_days: 14
  continue_on_error: true
  log_level: normal
```

### `fetch` (mapping, required)

Where the source code comes from. Two `type` values are accepted.

Common to both:

| Field  | Type   | Required | Notes                  |
|--------|--------|----------|------------------------|
| `type` | enum   | yes      | `git` or `local`.      |

`git` type:

| Field           | Type   | Default | Notes                                                       |
|-----------------|--------|---------|-------------------------------------------------------------|
| `url`           | string | —       | Required. Git URL (HTTPS or SSH).                           |
| `branch`        | string | `main`  | Branch to check out.                                        |
| `shallow_clone` | bool   | `true`  | When `true`, only `depth` commits are fetched.              |
| `depth`         | int    | `1`     | Must be `> 0`. Honoured only when `shallow_clone` is true.  |
| `ssh_key_path`  | string | unset   | Path to a private key. Tilde expansion supported.           |

`local` type:

| Field        | Type   | Default | Notes                                                         |
|--------------|--------|---------|---------------------------------------------------------------|
| `local_path` | string | —       | Required. Must exist at validation time. Tilde expansion ok.  |

```yaml
fetch:
  type: git
  url: git@github.com:owner/repo.git
  branch: develop
  shallow_clone: true
  depth: 1
  ssh_key_path: ~/.ssh/id_ed25519_sentinel
```

```yaml
fetch:
  type: local
  local_path: /work/checkouts/my_project
```

The fetched code lands under `<run_dir>/repos/<repo_name>/` and the
absolute path is recorded on the per-run `RunContext` (as
`ctx.repo_path`) so downstream phases can read it without touching the
config dict.

### `project_manifest` (string, required)

Path, relative to the fetched repo's root, to the project's canonical
YAML manifest (the aurig-core schema). Sentinel only resolves the path
and surfaces it to phases that need it; it does not read or validate
the manifest's contents.

```yaml
project_manifest: manifest/sentinel.yaml
```

### `pre_run` (mapping, optional)

Hooks executed after `fetch` and before any pipeline phase. Useful for
sourcing vendor environment files, generating register maps, or
priming a build directory.

| Field             | Type            | Default | Notes                                                                          |
|-------------------|-----------------|---------|--------------------------------------------------------------------------------|
| `enabled`         | bool            | `false` | When `false`, the entire block is skipped.                                     |
| `scripts`         | list of strings | `[]`    | Paths run in order before `program`. Resolved against repo root.               |
| `program`         | string          | unset   | Main executable to run last.                                                   |
| `args`            | list of strings | `[]`    | Arguments passed to `program`.                                                 |
| `timeout_seconds` | positive int    | `1800`  | Per-command wall-clock timeout. Applied independently to each script and to `program`. A timeout converts to a phase failure that respects `global_settings.continue_on_error`, so one hung hook can't freeze the rest of a nightly batch. |

Scripts are dispatched by extension: `.py` runs under the active Python
interpreter, `.ps1` and `.bat`/`.cmd` use PowerShell/cmd on Windows,
`.sh` and extensionless files run under bash on POSIX.

When `enabled: true` the validator requires at least one of `scripts`
(non-empty) or `program`; an enabled-but-empty block fails
validation. The runner skips that config (counted as `skipped
(validation)` in the aggregate summary) instead of executing the
phase as a silent no-op. Note that a skipped config does not by
itself escalate the exit code — see the [Exit codes](#exit-codes)
table for the full contract.

```yaml
pre_run:
  enabled: true
  scripts:
    - scripts/load_vivado_env.sh
    - scripts/gen_regmap.py
  program: vivado
  args: ["-mode", "batch", "-source", "scripts/create_project.tcl"]
  timeout_seconds: 3600   # default 1800; raise for long environment scripts
```

### `phases` (mapping, required)

At least one phase must have `enabled: true`. Unknown phase names are
rejected. Every phase accepts an `enabled` boolean (default `false`);
phases with backends accept additional fields documented below.

#### `phases.linting`

Drives [aurig-core](https://github.com/aurig-fpga/aurig-core)'s project
lint runner (`tools/run_lint_project_inprocess.tcl`) as a subprocess.
Sentinel passes the top-level `project_manifest` through as
`-manifest`, captures stdout+stderr into `<run_dir>/logs/lint.log`,
and maps the runner's exit codes onto phase statuses (`0` →
`completed`, `1` → `failed`, `2` → `error`).

| Field            | Type            | Default        | Notes                                                                              |
|------------------|-----------------|----------------|------------------------------------------------------------------------------------|
| `enabled`        | bool            | `false`        |                                                                                    |
| `tcl4fpga_path`  | string          | unset          | Required at runtime. Falls back to env `SENTINEL_TCL4FPGA_PATH` when absent.       |
| `tclsh_path`     | string          | `tclsh`        | Resolved on `PATH` at runtime.                                                     |
| `fail_on`        | enum            | `error`        | `error \| warning \| note \| any \| none`. Passed through as aurig-core `-fail_on`. Default mirrors the aurig-core single-file CLI. |
| `format`         | enum            | `html`         | `html \| md \| csv \| text`. Passed through as `-format`.                          |
| `output_dir`     | string          | `lint_output`  | Created under the run dir. Bundled into `artifacts/` by `bundle_zip`.              |
| `policy`         | string          | unset          | Path to an aurig-core policy JSON. Relative paths are resolved against the fetched repo root (matching the `project_manifest` convention); absolute paths pass through unchanged for shared policies outside the repo. Passed through as `-policy`. |
| `include`        | string          | unset          | Regex passed through as `-include`.                                                |
| `exclude`        | string          | unset          | Regex passed through as `-exclude` (OR'd with `lint.excludes` from the manifest).  |

`tcl4fpga_path` must point at the root of an aurig-core checkout; the
runner script is resolved as
`<tcl4fpga_path>/tools/run_lint_project_inprocess.tcl`. aurig-core
itself requires `tclsh` 8.5+ and (recommended) `tcllib`.

The baseline workflow exposed by aurig-core's runner (`-baseline`,
`-only_new`, `-update_baseline`) is intentionally not surfaced in the
v1 Sentinel schema; the first nightly run does not need incremental
CI. Wiring those flags into the YAML lands as a follow-up OP if a
customer asks for it.

```yaml
phases:
  linting:
    enabled: true
    tcl4fpga_path: /opt/aurig-core
    fail_on: error
    format: html
    output_dir: lint_output
    # policy: lint/lint_user.json
    # include: '.*\.vhd$'
    # exclude: '.*generated.*'
```

#### `phases.documentation`

Pending. Reports `PENDING` when enabled. No backend-specific fields are
read yet.

#### `phases.regression`

| Field              | Type            | Default              | Notes                                                                       |
|--------------------|-----------------|----------------------|-----------------------------------------------------------------------------|
| `enabled`          | bool            | `false`              |                                                                             |
| `backend`          | enum            | `convention`         | `vunit` or `convention`.                                                    |
| `simulator`        | enum            | backend-dependent    | `ghdl`, `modelsim`, `questasim`, `active-hdl`, `vivado`.                    |
| `testbench_dir`    | string          | —                    | Required for `convention`. Relative to the fetched repo.                    |
| `vunit_run_script` | string          | —                    | Required for `vunit`. Path to a VUnit `run.py`-style script inside the repo.|
| `vunit_args`       | list of strings | `[]`                 | Extra args passed to the VUnit script.                                      |
| `work_dir`         | string          | `regression_output`  | Output directory under the run dir.                                         |
| `options`          | mapping         | `{}`                 | See below.                                                                  |

`options.log2file_sim_output` (bool, default `true`) writes the
simulator's stdout/stderr to a per-testbench log file.

The `convention` backend discovers `tb_*.vhd` and `tb_*.vhdl` under
`<repo>/<testbench_dir>/` and expects a sibling `<tb_name>_sim.tcl` for
each. The simulator command is chosen by name: `vsim -c -do <script>`
for ModelSim/Questa, `ghdl --elab-run <tb>` for GHDL, `vsimsa -do
<script>` for Active-HDL, `xsim <tb> -tclbatch <script>` for Vivado.
Per-testbench timeout is 10 minutes; the global VUnit timeout is one
hour.

```yaml
phases:
  regression:
    enabled: true
    backend: convention
    simulator: ghdl
    testbench_dir: sim
    options:
      log2file_sim_output: true
```

#### `phases.synthesis`

Sentinel sets up the environment and invokes the vendor tool against a
TCL script that the project's repository owns. Sentinel does not
generate the synthesis script.

| Field                   | Type            | Default              | Notes                                                                              |
|-------------------------|-----------------|----------------------|------------------------------------------------------------------------------------|
| `enabled`               | bool            | `false`              |                                                                                    |
| `synthesis_tool`        | enum            | `vivado`             | `vivado` or `quartus` (stub).                                                      |
| `synthesis_tool_path`   | string          | —                    | Required at runtime. Used to prepend `<path>/bin` to `PATH`.                       |
| `synthesis_script`      | string          | `vivado`             | Executable name invoked from `PATH` (`vivado`, in normal use).                     |
| `repo_synthesis_script` | string          | auto-discovered      | Path inside the repo. Falls back to `scripts/run_synthesis.tcl` and similar.       |
| `output_dir`            | string          | `synthesis_output`   | Created under the run dir.                                                         |
| `options.expected_reports` | list of strings | `[]`             | Names checked under `output_dir`; missing reports are warnings, not failures.      |

Sentinel runs the tool in batch mode:

```
<synthesis_script> -mode batch -source <repo_synthesis_script> -log vivado.log -journal vivado.jou
```

with the synthesis tool path prepended to `PATH`. The repo-provided
script must exit with `0` on success.

```yaml
phases:
  synthesis:
    enabled: true
    synthesis_tool: vivado
    synthesis_tool_path: /tools/Xilinx/Vivado/2024.1
    synthesis_script: vivado
    repo_synthesis_script: scripts/run_synthesis.tcl
    output_dir: synthesis_output
    options:
      expected_reports:
        - utilization.rpt
        - timing_summary.rpt
        - drc.rpt
```

#### `phases.deployment`

Roadmap. Reports `PENDING` when enabled. Schema-level fields will be
defined when the backend is wired in.

### `output` (mapping, optional)

| Field         | Type   | Default  | Notes                                                |
|---------------|--------|----------|------------------------------------------------------|
| `base_dir`    | string | `./runs` | Root for all run dirs. Resolved relative to CWD.     |
| `bundle_zip`  | bool   | `true`   | When `true`, zips `<run_dir>/artifacts/` at the end. |

Each run lands at
`<base_dir>/<project.name>/<YYYY-MM-DD_HHMMSS>/`, with a `logs/`
subdirectory and a per-phase output directory.

```yaml
output:
  base_dir: /var/lib/sentinel/runs
  bundle_zip: true
```

## Project Structure

```
Sentinel/
+-- README.md
+-- run.py                       # convenience entry point (also: sentinel CLI)
+-- pyproject.toml
+-- configs/                     # default config discovery directory
|   +-- example.yaml             # canonical schema example
|   +-- disabled/                # parked configs, ignored by discovery
+-- sentinel/                    # Python package
|   +-- __init__.py
|   +-- main.py                  # CLI, multi-config orchestration
|   +-- config_validator.py      # YAML schema, loading, discovery
|   +-- fetch_code.py            # git/local fetch
|   +-- project_setup.py         # pre_run hook execution
|   +-- regression_testing.py    # vunit + convention backends
|   +-- synthesis.py             # Vivado custom-script backend
+-- deployment/                  # systemd / Task Scheduler scaffolding
+-- tests/
    +-- test_sentinel_comprehensive.py
    +-- fixtures/vhdl/           # small fixture VHDL files
```

## Roadmap

### Currently active

- Multi-config discovery, validation, and skip-and-continue execution.
- Git and local `fetch` with optional SSH key.
- `pre_run` hooks (scripts + main program) with cross-platform script
  dispatch.
- `linting` phase via `aurig-core`'s project lint runner (subprocess).
- `regression` phase with VUnit and convention backends across GHDL,
  ModelSim/Questa, Active-HDL, and Vivado xsim.
- `synthesis` phase against repository-provided TCL scripts on Vivado.
- Time-window gating, retention-based cleanup, artifact bundling.

### In progress

- `documentation` backend via `aurig-core` subprocess invocation.

### Planned

- Multi-vendor synthesis (Quartus first, then Diamond).
- `deployment` phase: bitstream programming and post-program verify.
- Post-deployment testing phase (board-in-the-loop sanity checks).
- AI-assisted analysis layer: regression diff, anomaly detection,
  suggested fixes synthesised from logs and reports.

## Architecture Decisions

**Why YAML over JSON.** Configs are written and reviewed by humans.
YAML's comments, anchors, and indented structure make per-project
files self-documenting and reduce diff noise during routine edits.
Schema versioning (`schema_version: "1.0"`) gives us a clean break
when v2 lands.

**Why Sentinel does not know about VHDL.** The HDL-aware work — source
discovery, library mapping, synthesis script generation — is
delegated to `aurig-core` and to the project's own canonical manifest.
Sentinel's job is to schedule, fetch, run, and capture. Two narrow
layers compose better than one wide one, and changes to HDL tooling
do not ripple into the runner.

**Why per-project YAML files.** A monolithic config would couple
unrelated projects: a typo in one section would break every nightly
run. One file per project lets the runner skip-and-continue at the
file level, keeps blame attribution clean in git, and makes the
disabled/ convention possible without any code path change.

**Why filesystem-as-database for `configs/disabled/`.** No state to
keep elsewhere, no UI to write, no migration. Moving a file is the
operation; the runner's only contribution is to ignore subdirectories
during discovery.

## Contributing

Sentinel is part of the AURIG open-source FPGA tooling stack by
LogiMentor S.r.l. Contact the team for contribution guidelines,
issue triage, and release coordination.

## License

Sentinel is licensed under the Apache License 2.0.

Copyright 2026 LogiMentor S.r.l.

See [LICENSE](./LICENSE) and [NOTICE](./NOTICE) for details.
