"""API tests run against a throwaway SQLite database and temp directories —
no Postgres, Redis or ML stack needed. The environment must be set before
`api.main` (and so `models.db`) is first imported."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="vap-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["REFERENCE_FRAME_DIR"] = os.path.join(_TMP, "frames")
os.environ["VIDEO_STORAGE_ROOT"] = os.path.join(_TMP, "uploads")
