"""Regression test for a real bug found during live-Docker verification: the worker
process only imports app.workers.tasks (per celery_app.py's `include=`), which only
imports Document/Profile/ProfileField directly — never app.models.user. SQLAlchemy can't
resolve Document.user_id's ForeignKey("users.id") until the `users` table is registered
in the same Base.metadata, so a real worker crashed with NoReferencedTableError on its
first query, even though every other test in this suite passed.

Every other test in this file (and conftest.py's db_engine fixture) imports
app.db.base directly, which masks this exact gap by populating Base.metadata before any
test gets a chance to exercise the worker's own, narrower import graph. A subprocess with
a fresh interpreter is the only way to catch it — importing app.workers.celery_app must,
by itself, register every model.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_worker_tasks_registers_all_models_for_fk_resolution():
    # Import app.workers.tasks directly — this is what Celery's `include=` list actually
    # loads at worker boot, and it's the module that (before the fix) only pulled in
    # Document/Profile/ProfileField, never User. Importing celery_app alone wouldn't
    # reproduce the bug: at that point nothing has touched Document yet, so
    # configure_mappers() would trivially pass with zero mappers registered.
    script = (
        "from app.workers.tasks import ocr_extract_task\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "configure_mappers()\n"  # raises NoReferencedTableError if any FK target is missing
        "from app.db.base_class import Base\n"
        "assert 'users' in Base.metadata.tables, 'users table not registered'\n"
        "assert 'documents' in Base.metadata.tables, 'documents table not registered'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=".",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
