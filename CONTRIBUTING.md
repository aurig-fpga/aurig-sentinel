<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 LogiMentor S.r.l. -->

# Contributing to AURIG Sentinel

Thanks for your interest in contributing. This document explains how to set
up a development environment, the branch and pull request workflow, and the
conventions we follow.

By participating in this project you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- Report bugs and request features via GitHub issues.
- Improve documentation.
- Submit bug fixes or new features via pull requests.

## Development setup

1. Clone the repository.
2. Use Python 3.9 or newer.
3. Install the package in editable mode with the test dependency:

   ```sh
   pip install -e .[test]
   ```

4. Run the test suite:

   ```sh
   python -m pytest tests/ -v
   ```

## Branch and pull request workflow

- Create a feature branch off `main`. Use one of the following type
  prefixes — they apply to both branch names (slash form, e.g.
  `docs/add-changelog`) and commit subjects / PR titles (colon form,
  e.g. `docs: add changelog`):
  - `chore` — maintenance, tooling, no code change
  - `docs` — documentation only
  - `ci` — CI configuration
  - `fix` — bug fix
  - `feat` — new feature
  - `refactor` — code restructuring without behavior change
  - `test` — adding or fixing tests
  - `perf` — performance improvement
- Open a pull request against `main`.
- All CI checks must pass before a pull request can be merged.
- Pull requests are squash-merged: each PR becomes a single commit on
  `main`, with the commit title and message taken from the PR title and
  description. Intermediate commits on the feature branch don't need to
  be tidy — they get collapsed at merge.

## Commit message guidelines

- Write clear, conventional commit messages.
- Use the imperative mood (e.g. `add foo`, not `added foo`).
- Keep the subject line between 50 and 72 characters.
- Keep each pull request to one logical change.

## Reporting bugs and requesting features

Open a GitHub issue. For bugs, include steps to reproduce, what you expected,
and what actually happened. For features, describe the use case and the
behavior you would like.
