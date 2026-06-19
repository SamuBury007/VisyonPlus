#!/bin/bash
set -e
pip install flask playwright requests
python -m playwright install chromium
python -m playwright install-deps chromium
