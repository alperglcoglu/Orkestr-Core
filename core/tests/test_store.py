"""SQLite store: migrations, constraints, transactions, append-only audit (acceptance 3)."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from orkestr_core.domain import state as st
from orkestr_core.domain.errors import OrkestrError
from orkestr_core.store import db

from phase1_helpers import P


def test_migrations_are_recorded_and_idempotent(tmp_path):
    p = P(tmp_path)
    conn = p.project.store.connect()
    try:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        assert versions == [1]
        assert db.migrate(conn) == []
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_newer_schema_is_refused(tmp_path):
    p = P(tmp_path)
    p.project.store.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
    with pytest.raises(OrkestrError, match="newer than this orkestr-core"):
        p.project.store.connect()


def test_constraints_reject_illegal_rows(tmp_path):
    p = P(tmp_path)
    p.plan()
    store = p.project.store
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE task SET status = 'DONE' WHERE id = 'T-001'")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE task SET status = 'READY', authorized = 0 WHERE id = 'T-001'")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE task SET status = 'BLOCKED', blocked_reason = NULL WHERE id = 'T-001'")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE project SET status = 'PAUSED', status_reason = NULL")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("INSERT INTO task_dependency VALUES ('T-001', 'T-001', 0)")


def test_a_failed_mutation_rolls_back_everything(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.plan()

    def half_done(s):
        st.transition(s, "T-001", "IN_PROGRESS")
        raise OrkestrError("boom")

    with pytest.raises(OrkestrError, match="boom"):
        p.do(half_done)
    assert p.task("T-001")["status"] == "READY"
    assert not [e for e in p.history() if e["to"] == "IN_PROGRESS"]


def test_the_audit_log_is_append_only(tmp_path):
    p = P(tmp_path)
    store = p.project.store
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE audit_event SET summary = 'rewritten'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("DELETE FROM audit_event")


def test_closed_approvals_cannot_change_in_the_database(tmp_path):
    p = P(tmp_path)
    p.do(lambda s: st.request_approval(s, action="push", category="git_push", reason="release"))
    p.project.decide_approval("A-001", "reject", "no")
    with pytest.raises(sqlite3.IntegrityError, match="only PENDING"):
        p.project.store.execute("UPDATE approval SET status = 'GRANTED' WHERE id = 'A-001'")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        p.project.store.execute("DELETE FROM approval")


def test_concurrent_writers_are_serialized(tmp_path):
    p = P(tmp_path, autonomy="bounded")
    p.plan({"tasks": [{"title": f"T{i}", "owner": "qa-engineer", "acceptance_criteria": ["x"]} for i in range(1)]})
    errors = []

    def writer(n):
        try:
            for i in range(5):
                p.project.store.mutate(lambda s: st.add_note(s, "T-001", author="qa-engineer", text=f"writer {n} note {i}"))
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(p.task("T-001")["notes"]) == 20
    assert len([e for e in p.history() if e["type"] == "task.note"]) == 20


def test_the_core_refuses_to_write_a_project_the_plugin_manages(tmp_path):
    p = P(tmp_path)
    p.project.export(release=True)
    with pytest.raises(OrkestrError, match="managed by the plugin"):
        p.do(lambda s: st.add_assumption(s, "x"))
