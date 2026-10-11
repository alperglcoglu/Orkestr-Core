"""Repositories: the only module that writes SQL (PHASE3_IMPLEMENTATION.md §5).

`Store.mutate(fn)` loads the whole project into a domain State inside one write
transaction, runs `fn`, and writes the result back, together with its audit events. Every
string is redacted before it reaches the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..constants import DATA_DIR_NAME, DB_FILE
from ..domain.errors import OrkestrError, now
from ..domain.redact import redact
from ..domain.state import State
from . import db

LESSONS_HEADER = "# Lessons learned\n\nAppended by orkestr retrospectives. Each lesson links to its evidence.\n"


def _j(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _uj(text):
    return None if text is None else json.loads(text)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.data_dir = self.root / DATA_DIR_NAME
        self.path = self.data_dir / DB_FILE

    # -- lifecycle -----------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise OrkestrError(f"No orkestr-core database in {self.root} (expected {DATA_DIR_NAME}/{DB_FILE}).", "E_NO_PROJECT")
        conn = db.connect(self.path)
        db.migrate(conn)
        return conn

    def create(self, state: State, history: list[dict] | None = None) -> None:
        """Creates the database for a new or imported project. Refuses to overwrite."""
        if self.path.exists():
            raise OrkestrError(f"{DATA_DIR_NAME}/{DB_FILE} already exists; refusing to overwrite an existing project.", "E_EXISTS")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = db.connect(self.path)
        try:
            db.migrate(conn)
            with db.transaction(conn):
                self._save(conn, state, run_id=None, all_records=True)
                for ev in history or []:
                    self._insert_event(conn, ev, None)
        except BaseException:
            conn.close()
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            raise
        conn.close()

    # -- state ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection | None = None) -> State:
        own = conn is None
        conn = conn or self.connect()
        try:
            return self._load(conn)
        finally:
            if own:
                conn.close()

    def mutate(self, fn: Callable[[State], Any], run_id: int | None = None, require_core: bool = True) -> Any:
        conn = self.connect()
        try:
            with db.transaction(conn):
                state = self._load(conn)
                if require_core and state.project.get("managed_by") != "core":
                    raise OrkestrError(
                        "This project is managed by the plugin (managed_by=plugin). Run `orkestr-core import` to hand it to the core.",
                        "E_NOT_OWNER",
                    )
                result = fn(state)
                state.project["updated_at"] = now()
                self._save(conn, state, run_id)
            self._append_lessons(state)
            return result
        finally:
            conn.close()

    def _append_lessons(self, state: State):
        if not state.lesson_blocks:
            return
        file = self.data_dir / "memory" / "lessons-learned.md"
        file.parent.mkdir(parents=True, exist_ok=True)
        existing = file.read_text(encoding="utf-8") if file.exists() else LESSONS_HEADER
        text = existing.rstrip() + "\n\n" + "\n\n".join(b.rstrip() for b in state.lesson_blocks) + "\n"
        file.write_text(redact(text), encoding="utf-8")
        self.record_artifact("memory/lessons-learned.md", "lessons", file.read_text(encoding="utf-8"), "orchestrator")

    def _load(self, conn: sqlite3.Connection) -> State:
        row = conn.execute("SELECT * FROM project WHERE id = 1").fetchone()
        if not row:
            raise OrkestrError("The database has no project record.", "E_CORRUPT")
        items = conn.execute("SELECT kind, text FROM project_list_item ORDER BY kind, position").fetchall()
        project: dict = {
            "schema_version": row["schema_version"],
            "name": row["name"],
            "goal": row["goal"],
            "status": row["status"],
            "status_reason": row["status_reason"],
        }
        if row["autonomy"] is not None:
            project["autonomy"] = row["autonomy"]
        project["constraints"] = [r["text"] for r in items if r["kind"] == "constraint"]
        project["success_criteria"] = [r["text"] for r in items if r["kind"] == "success_criterion"]
        project["assumptions"] = [
            {"text": r["text"], "impact": r["impact"], "task": r["task_id"], "at": r["at"]}
            for r in conn.execute("SELECT * FROM assumption ORDER BY id")
        ]
        project["limits"] = json.loads(row["limits_json"])
        project["counters"] = json.loads(row["counters_json"])
        project["created_at"] = row["created_at"]
        project["updated_at"] = row["updated_at"]
        project["managed_by"] = row["managed_by"]

        def grouped(sql: str) -> dict[str, list]:
            out: dict[str, list] = {}
            for r in conn.execute(sql):
                out.setdefault(r["task_id"], []).append(r)
            return out

        deps = grouped("SELECT task_id, depends_on_id FROM task_dependency ORDER BY task_id, position")
        scopes = grouped("SELECT task_id, pattern FROM task_scope ORDER BY task_id, position")
        criteria = grouped("SELECT task_id, text FROM task_acceptance_criterion ORDER BY task_id, position")
        notes = grouped("SELECT * FROM task_note ORDER BY task_id, position")
        evidence = grouped("SELECT * FROM evidence ORDER BY task_id, position")

        tasks = []
        for r in conn.execute("SELECT * FROM task ORDER BY seq"):
            tid = r["id"]
            t = {
                "id": tid,
                "title": r["title"],
                "description": r["description"],
                "owner": r["owner"],
                "status": r["status"],
                "depends_on": [d["depends_on_id"] for d in deps.get(tid, [])],
                "acceptance_criteria": [c["text"] for c in criteria.get(tid, [])],
                "scope": [s["pattern"] for s in scopes.get(tid, [])],
                "requires_approval": bool(r["requires_approval"]),
                "approval_id": r["approval_id"],
                "authorized": bool(r["authorized"]),
                "source": {"type": r["source_type"], "ref": r["source_ref"]},
                "decision_id": r["decision_id"],
                "blocked_reason": r["blocked_reason"],
                "blocked_by_dependency": r["blocked_by_dependency"],
                "failure": _uj(r["failure_json"]),
                "fix_attempts": r["fix_attempts"],
                "recovery_meetings": r["recovery_meetings"],
                "evidence": [],
                "notes": [{"author": n["author"], "text": n["text"], "at": n["at"]} for n in notes.get(tid, [])],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for e in evidence.get(tid, []):
                entry = {"type": e["type"], "result": e["result"], "command": e["command"], "summary": e["summary"], "ref": e["ref"], "at": e["at"]}
                if e["test_result_id"] is not None:
                    entry["test_result_id"] = e["test_result_id"]
                t["evidence"].append(entry)
            verify = _uj(r["verify_json"])
            if verify:
                t["verify"] = verify
            tasks.append(t)

        approvals = [
            {
                "id": r["id"],
                "action": r["action"],
                "category": r["category"],
                "reason": r["reason"],
                "consequences": r["consequences"],
                "risks": r["risks"],
                "requested_by": r["requested_by"],
                "task_id": r["task_id"],
                "status": r["status"],
                "decided_by": r["decided_by"],
                "decided_at": r["decided_at"],
                "decision_note": r["decision_note"],
                "execution_result": r["execution_result"],
                "created_at": r["created_at"],
            }
            for r in conn.execute("SELECT * FROM approval ORDER BY created_at, id")
        ]

        def bodies(table: str) -> dict[str, dict]:
            return {r["id"]: json.loads(r["body_json"]) for r in conn.execute(f"SELECT id, body_json FROM {table} ORDER BY id")}

        return State(
            project=project,
            tasks=tasks,
            approvals=approvals,
            meetings=bodies("meeting"),
            decisions=bodies("decision"),
            reviews=bodies("review"),
            retros=bodies("retro"),
        )

    def _save(self, conn: sqlite3.Connection, state: State, run_id: int | None, all_records: bool = False):
        p = redact(state.project)
        conn.execute(
            """INSERT INTO project (id, schema_version, name, goal, status, status_reason, autonomy, managed_by,
                                    limits_json, counters_json, created_at, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET schema_version=excluded.schema_version, name=excluded.name, goal=excluded.goal,
                 status=excluded.status, status_reason=excluded.status_reason, autonomy=excluded.autonomy,
                 managed_by=excluded.managed_by, limits_json=excluded.limits_json, counters_json=excluded.counters_json,
                 created_at=excluded.created_at, updated_at=excluded.updated_at""",
            (
                p.get("schema_version", 1),
                p["name"],
                p["goal"],
                p["status"],
                p.get("status_reason"),
                p.get("autonomy"),
                p.get("managed_by") or "core",
                _j(p["limits"]),
                _j(p["counters"]),
                p["created_at"],
                p["updated_at"],
            ),
        )
        conn.execute("DELETE FROM project_list_item")
        for kind, key in (("constraint", "constraints"), ("success_criterion", "success_criteria")):
            for i, text in enumerate(p.get(key) or []):
                conn.execute("INSERT INTO project_list_item VALUES (?, ?, ?)", (kind, i, text))
        conn.execute("DELETE FROM assumption")
        for a in p.get("assumptions") or []:
            conn.execute("INSERT INTO assumption (text, impact, task_id, at) VALUES (?, ?, ?, ?)", (a["text"], a.get("impact"), a.get("task"), a["at"]))

        for table in ("task_dependency", "task_scope", "task_acceptance_criterion", "task_note", "evidence"):
            conn.execute(f"DELETE FROM {table}")
        for seq, t in enumerate(redact(state.tasks)):
            conn.execute(
                """INSERT INTO task (id, seq, title, description, owner, status, requires_approval, approval_id, authorized,
                                     source_type, source_ref, decision_id, blocked_reason, blocked_by_dependency, failure_json,
                                     fix_attempts, recovery_meetings, verify_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET seq=excluded.seq, title=excluded.title, description=excluded.description,
                     owner=excluded.owner, status=excluded.status, requires_approval=excluded.requires_approval,
                     approval_id=excluded.approval_id, authorized=excluded.authorized, source_type=excluded.source_type,
                     source_ref=excluded.source_ref, decision_id=excluded.decision_id, blocked_reason=excluded.blocked_reason,
                     blocked_by_dependency=excluded.blocked_by_dependency, failure_json=excluded.failure_json,
                     fix_attempts=excluded.fix_attempts, recovery_meetings=excluded.recovery_meetings,
                     verify_json=excluded.verify_json, updated_at=excluded.updated_at""",
                (
                    t["id"],
                    seq,
                    t["title"],
                    t.get("description") or "",
                    t["owner"],
                    t["status"],
                    int(bool(t["requires_approval"])),
                    t.get("approval_id"),
                    int(bool(t["authorized"])),
                    t["source"]["type"],
                    t["source"].get("ref"),
                    t.get("decision_id"),
                    t.get("blocked_reason"),
                    t.get("blocked_by_dependency"),
                    _j(t.get("failure")),
                    t["fix_attempts"],
                    t.get("recovery_meetings", 0),
                    _j(t.get("verify")) if t.get("verify") else None,
                    t["created_at"],
                    t["updated_at"],
                ),
            )
            for i, d in enumerate(t["depends_on"]):
                conn.execute("INSERT INTO task_dependency VALUES (?, ?, ?)", (t["id"], d, i))
            for i, s in enumerate(t.get("scope") or []):
                conn.execute("INSERT INTO task_scope VALUES (?, ?, ?)", (t["id"], i, s))
            for i, c in enumerate(t["acceptance_criteria"]):
                conn.execute("INSERT INTO task_acceptance_criterion VALUES (?, ?, ?)", (t["id"], i, c))
            for i, n in enumerate(t.get("notes") or []):
                conn.execute("INSERT INTO task_note VALUES (?, ?, ?, ?, ?)", (t["id"], i, n["author"], n["text"], n["at"]))
            for i, e in enumerate(t["evidence"]):
                conn.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (t["id"], i, e["type"], e["result"], e.get("command"), e["summary"], e.get("ref"), e.get("test_result_id"), e["at"]),
                )

        for a in redact(state.approvals):
            conn.execute(
                """INSERT INTO approval (id, action, category, reason, consequences, risks, requested_by, task_id, status,
                                         decided_by, decided_at, decision_note, execution_result, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET action=excluded.action, category=excluded.category, reason=excluded.reason,
                     consequences=excluded.consequences, risks=excluded.risks, task_id=excluded.task_id, status=excluded.status,
                     decided_by=excluded.decided_by, decided_at=excluded.decided_at, decision_note=excluded.decision_note,
                     execution_result=excluded.execution_result""",
                (
                    a["id"],
                    a["action"],
                    a["category"],
                    a["reason"],
                    a.get("consequences") or "",
                    a.get("risks") or "",
                    a.get("requested_by") or "orchestrator",
                    a.get("task_id"),
                    a["status"],
                    a.get("decided_by"),
                    a.get("decided_at"),
                    a.get("decision_note"),
                    a.get("execution_result"),
                    a["created_at"],
                ),
            )

        # Records: those changed in this mutation (all of them on create/import).
        changed = (
            {(f, rid) for f in ("meetings", "decisions", "reviews", "retros") for rid in getattr(state, f)}
            if all_records
            else state.changed_records
        )
        for folder, rid in sorted(changed):
            self._save_record(conn, folder, redact(getattr(state, folder)[rid]))

        for ev in state.events:
            self._insert_event(conn, ev, run_id)

    def _save_record(self, conn: sqlite3.Connection, folder: str, r: dict):
        body = _j(r)
        if folder == "meetings":
            conn.execute(
                "INSERT INTO meeting VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json",
                (r["id"], r["date"], r["objective"], r["context"], r["trigger"], r["outcome"], r.get("decision_id"), r.get("recovery_for"), body),
            )
            conn.execute("DELETE FROM meeting_participant WHERE meeting_id = ?", (r["id"],))
            conn.execute("DELETE FROM meeting_contribution WHERE meeting_id = ?", (r["id"],))
            for p in r["participants"]:
                conn.execute("INSERT INTO meeting_participant VALUES (?, ?, ?)", (r["id"], p["role"], p["responsibility"]))
            for rnd in r["rounds"]:
                for c in rnd["contributions"]:
                    conn.execute(
                        "INSERT OR REPLACE INTO meeting_contribution VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (r["id"], rnd["round"], c["role"], c["position"], _j(c.get("key_points") or []), _j(c.get("concerns") or []), c.get("agent_execution_id")),
                    )
        elif folder == "decisions":
            conn.execute(
                """INSERT INTO decision VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET selected_option=excluded.selected_option, approval_status=excluded.approval_status,
                     decided_by=excluded.decided_by, decided_at=excluded.decided_at, note=excluded.note, body_json=excluded.body_json""",
                (
                    r["id"],
                    r["date"],
                    r["question"],
                    r.get("selected_option"),
                    r["approval_status"],
                    int(bool(r.get("founder_required"))),
                    r.get("meeting_id"),
                    r.get("recovery_for"),
                    r.get("decided_by"),
                    r.get("decided_at"),
                    r.get("note"),
                    body,
                ),
            )
            conn.execute("DELETE FROM decision_task WHERE decision_id = ?", (r["id"],))
            for tid in r.get("task_ids") or []:
                conn.execute("INSERT OR IGNORE INTO decision_task VALUES (?, ?, 'created')", (r["id"], tid))
            for tid in r.get("applies_to") or []:
                conn.execute("INSERT OR IGNORE INTO decision_task VALUES (?, ?, 'applies_to')", (r["id"], tid))
        elif folder == "reviews":
            conn.execute(
                "INSERT INTO review VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json",
                (r["id"], r["date"], r.get("task_id"), r["verdict"], r["scope"], _j(r["reviewers"]), _j(r.get("assumptions") or []), r.get("agent_execution_id"), body),
            )
            conn.execute("DELETE FROM review_finding WHERE review_id = ?", (r["id"],))
            for i, f in enumerate(r["findings"]):
                conn.execute(
                    "INSERT INTO review_finding VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], i, f["title"], f["severity"], f["status"], f.get("component"), f["evidence"], f.get("recommendation")),
                )
        elif folder == "retros":
            conn.execute(
                "INSERT INTO retro VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json",
                (r["id"], r["date"], r["scope"], body),
            )

    def _insert_event(self, conn: sqlite3.Connection, ev: dict, run_id: int | None):
        ev = redact(ev)
        conn.execute(
            "INSERT INTO audit_event (ts, type, actor, entity, from_state, to_state, summary, data_json, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev["ts"], ev["type"], ev.get("actor"), ev.get("entity"), ev.get("from"), ev.get("to"), ev.get("summary") or "", _j(ev.get("data")), run_id),
        )

    # -- history and auxiliary records ----------------------------------------

    def history(self, after: str | None = None, limit: int | None = None) -> list[dict]:
        conn = self.connect()
        try:
            sql = "SELECT * FROM audit_event"
            args: list = []
            if after:
                sql += " WHERE ts > ?"
                args.append(after)
            sql += " ORDER BY id"
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
        events = [
            {
                "ts": r["ts"],
                "type": r["type"],
                "actor": r["actor"],
                "entity": r["entity"],
                "from": r["from_state"],
                "to": r["to_state"],
                "summary": r["summary"],
                "data": _uj(r["data_json"]),
            }
            for r in rows
        ]
        return events[-limit:] if limit else events

    def execute(self, sql: str, args: tuple = ()) -> int:
        """A single write outside a State mutation (runs, steps, executions, test results)."""
        conn = self.connect()
        try:
            with db.transaction(conn):
                cur = conn.execute(sql, args)
                return cur.lastrowid
        finally:
            conn.close()

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def record_test_result(self, task_id: str | None, command: str, exit_code: int | None, duration_ms: int, timed_out: bool, output_tail: str, run_by: str) -> int:
        return self.execute(
            "INSERT INTO test_result (task_id, command, exit_code, duration_ms, timed_out, output_tail, run_by, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, redact(command), exit_code, duration_ms, int(timed_out), redact(output_tail), run_by, now()),
        )

    def record_artifact(self, path: str, kind: str, text: str, updated_by: str):
        self.execute(
            """INSERT INTO artifact VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (path, kind, sha256_text(text), updated_by, now()),
        )

    def file_hashes(self) -> dict[str, str]:
        return {r["path"]: r["sha256"] for r in self.query("SELECT path, sha256 FROM file_sync")}

    def set_file_hashes(self, hashes: dict[str, str]):
        conn = self.connect()
        try:
            with db.transaction(conn):
                conn.execute("DELETE FROM file_sync")
                ts = now()
                for path, sha in hashes.items():
                    conn.execute("INSERT INTO file_sync VALUES (?, ?, ?)", (path, sha, ts))
        finally:
            conn.close()
