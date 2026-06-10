# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

# __init__.py

# This file indicates that the directory is a Python package.
# Import key functions for convenience but maintain module structure for testing

# Import modules (not individual functions) to preserve module namespace for mocking
from . import main
from . import fetch_code
from . import regression_testing
from . import synthesis
from . import project_setup

# Convenience imports for direct access
from .main import init_logging, execute_phases
from .run_context import RunContext
from .config_validator import (
    ConfigValidationError,
    discover_configs,
    load_and_validate,
    load_config_file,
    validate_config,
)

__all__ = [
    'main',
    'fetch_code',
    'regression_testing',
    'synthesis',
    'project_setup',
    'init_logging',
    'execute_phases',
    'RunContext',
    'ConfigValidationError',
    'discover_configs',
    'load_and_validate',
    'load_config_file',
    'validate_config',
]