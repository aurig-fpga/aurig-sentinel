# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

# run.py
import sys
import os

# Force working directory to the root of the repo
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "sentinel"))

from sentinel import main

if __name__ == "__main__":
    main.main()
