#!/bin/bash
set -e
pip install pip-tools safety
pip-compile --output-file requirements.txt requirements.in
pip-compile --output-file requirements-dev.txt requirements-dev.in
safety check -r requirements.txt
