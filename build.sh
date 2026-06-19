#!/bin/bash
set -e
pip install flask playwright requests
python -m playwright install --with-deps chromium
