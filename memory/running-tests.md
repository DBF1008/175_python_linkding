---
name: running-tests
description: How to run the linkding test suite and a cryptic huey error its setup avoids
metadata:
  type: reference
---

Run tests with `uv run pytest <paths>` (the project is uv-managed; `pytest.ini` sets `DJANGO_SETTINGS_MODULE = bookmarks.settings.dev`). `make test` runs `uv run pytest -n auto`.

Before the first run you must create the data directory tree (the `make init` target does this):
`mkdir -p data data/assets data/favicons data/previews`

Without `data/`, every test fails at import time with a cryptic `sqlite3.OperationalError: unable to open database file` — huey (`huey.contrib.djhuey`) opens `data/tasks.sqlite3` (see `bookmarks/settings/base.py` HUEY config) during `django.setup()`, before any test runs.
