"""Project operations shared by the CLI, the API, and the workflow engine."""

from __future__ import annotations

import secrets
from pathlib import Path

from .constants import (
    CORE_LIMITS,
    DATA_DIR_NAME,
    DB_FILE,
    DEFAULT_AUTONOMY,
    DEFAULT_LIMITS,
    LOCK_FILE,
    PREFIX,
    TOKEN_FILE,
)
from .domain import records as rec
from .domain import state as st
from .domain.errors import OrkestrError, non_empty, now
from .domain.render import render_report, render_status
from .domain.validate import validate_state
from .interop.files import data_dir, phase1_files, read_phase1, sha256_file, write_atomic
from .store.repo import Store

GITIGNORE = f"# Written by orkestr-core. Local database, API token, and lock never belong in Git.\n{DB_FILE}*\n{TOKEN_FILE}\n{LOCK_FILE}\n"


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.dir = data_dir(self.root)
        self.store = Store(self.root)

    # -- creation and hand-over ------------------------------------------------

    def init(self, name: str, goal: str, constraints=(), success=(), autonomy: str = DEFAULT_AUTONOMY) -> None:
        if not non_empty(name) or not non_empty(goal):
            raise OrkestrError("--name and --goal are required.", "E_USAGE")
        if autonomy not in ("bounded", "supervised"):
            raise OrkestrError("--autonomy must be one of bounded, supervised", "E_USAGE")
        if (self.dir / "project.json").exists() or self.store.exists():
            raise OrkestrError(f"{DATA_DIR_NAME}/ already holds a project; refusing to overwrite it.", "E_EXISTS")
        if self.dir.exists() and any(self.dir.iterdir()):
            raise OrkestrError(f"{DATA_DIR_NAME}/ exists but has no project; inspect it before initializing (nothing was changed).", "E_EXISTS")
        ts = now()
        project = {
            "schema_version": 1,
            "name": name.strip(),
            "goal": goal.strip(),
            "status": "ACTIVE",
            "status_reason": None,
            "autonomy": autonomy,
            "constraints": list(constraints),
            "success_criteria": list(success),
            "assumptions": [],
            "limits": {**DEFAULT_LIMITS, **CORE_LIMITS},
            "counters": {k: 0 for k in PREFIX.values()},
            "created_at": ts,
            "updated_at": ts,
            "managed_by": "core",
        }
        event = {"ts": ts, "type": "project.init", "actor": "orchestrator", "entity": "project", "from": None, "to": "ACTIVE", "summary": project["name"], "data": None}
        self.store.create(st.State(project=project), [event])
        self._write_private_files()
        self.export(force=True)

    def import_phase1(self) -> dict:
        """Hands a Phase 1 project from the plugin to the core."""
        if not (self.dir / "project.json").exists():
            raise OrkestrError(f"No Phase 1 project in {self.root} (expected {DATA_DIR_NAME}/project.json).", "E_NO_PROJECT")
        if (self.dir / ".lock").exists():
            raise OrkestrError("The plugin's state tool holds .orkestr/.lock; wait for it to finish (or remove a stale lock) and retry.", "E_LOCKED")
        backup = None
        if self.store.exists():
            current = self.store.load()
            if current.project.get("managed_by") == "core":
                raise OrkestrError("The core already manages this project.", "E_EXISTS")
            # Released earlier: keep the old database (it holds run and agent records) and start fresh.
            backup = self.store.path.with_name(f"{DB_FILE}.released-{now().replace(':', '').replace('.', '')}")
            self.store.path.rename(backup)
            for suffix in ("-wal", "-shm"):
                p = Path(str(self.store.path) + suffix)
                if p.exists():
                    p.rename(Path(str(backup) + suffix))
        state, history, problems = read_phase1(self.root)
        check = validate_state(state, history)
        errors = problems + check["errors"]
        if errors:
            raise OrkestrError("The Phase 1 project does not validate; fix it with the plugin first:\n- " + "\n- ".join(errors), "E_INVALID")
        state.project.setdefault("limits", {})
        for k, v in CORE_LIMITS.items():
            state.project["limits"].setdefault(k, v)
        state.project["managed_by"] = "core"
        history = [*history, {"ts": now(), "type": "core.import", "actor": "orchestrator", "entity": "project", "from": "plugin", "to": "core", "summary": "managed by orkestr-core", "data": None}]
        self.store.create(state, history)
        self._write_private_files()
        self.export(force=True)
        return {"tasks": len(state.tasks), "events": len(history), "backup": backup.name if backup else None}

    def export(self, release: bool = False, force: bool = False) -> list[str]:
        """Writes the Phase 1 mirror files. With release=True, hands the project back to the plugin."""
        if release:
            self.store.mutate(lambda s: self._set_owner(s, "plugin"))
        state = self.store.load()
        files = phase1_files(state, self.store.history())
        recorded = self.store.file_hashes()
        if not force:
            conflicts = []
            for rel in files:
                current = sha256_file(self.dir / rel)
                if current is not None and recorded.get(rel) not in (None, current):
                    conflicts.append(rel)
                elif current is not None and rel not in recorded:
                    conflicts.append(rel)
            if conflicts:
                raise OrkestrError(
                    "These files changed outside orkestr-core since the last sync; refusing to overwrite them:\n- "
                    + "\n- ".join(conflicts)
                    + "\nInspect them, then run `orkestr-core export --force` to overwrite or restore them by hand.",
                    "E_CONFLICT",
                )
        for rel, text in files.items():
            write_atomic(self.dir / rel, text)
        self.store.set_file_hashes({rel: sha256_file(self.dir / rel) for rel in files})
        return sorted(files)

    def _set_owner(self, s: st.State, owner: str):
        frm = s.project.get("managed_by")
        s.project["managed_by"] = owner
        st.emit(s, "core.release" if owner == "plugin" else "core.import", entity="project", frm=frm, to=owner, summary=f"managed by {owner}")

    def _write_private_files(self):
        (self.dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        token = self.dir / TOKEN_FILE
        if not token.exists():
            token.write_text(secrets.token_urlsafe(32), encoding="utf-8")

    def token(self) -> str:
        return (self.dir / TOKEN_FILE).read_text(encoding="utf-8").strip()

    # -- mutations -------------------------------------------------------------

    def mutate(self, fn, run_id: int | None = None, export: bool = True):
        result = self.store.mutate(fn, run_id=run_id)
        if export:
            self.export()
        return result

    def decide_approval(self, approval_id: str, decision: str, note: str | None = None):
        return self.mutate(lambda s: st.decide_approval(s, approval_id, decision, by="founder", note=note))

    def record_approval_result(self, approval_id: str, result: str):
        return self.mutate(lambda s: st.record_approval_result(s, approval_id, result, actor="founder"))

    def decide_decision(self, decision_id: str, action: str, option: str | None = None, note: str | None = None):
        return self.mutate(lambda s: rec.decide_decision(s, decision_id, action, by="founder", option=option, note=note))

    def authorize_task(self, task_id: str):
        return self.mutate(lambda s: st.authorize_task(s, task_id, by="founder"))

    def approve_plan(self, note: str | None = None):
        return self.mutate(lambda s: st.approve_plan(s, by="founder", note=note))

    def retry_task(self, task_id: str):
        return self.mutate(lambda s: st.transition(s, task_id, "READY", retry=True, actor="founder"))

    def cancel_project(self, reason: str):
        return self.mutate(lambda s: st.set_project_status(s, "cancel", reason=reason, by="founder"))

    # -- reports -----------------------------------------------------------------

    def validate(self) -> dict:
        state = self.store.load()
        result = validate_state(state, self.store.history())
        if state.project.get("managed_by") == "core":
            recorded = self.store.file_hashes()
            for rel, sha in recorded.items():
                if sha256_file(self.dir / rel) != sha:
                    result["warnings"].append(f"{rel}: mirror file differs from the last export (changed outside orkestr-core)")
        return result

    def latest_run(self) -> dict | None:
        rows = self.store.query("SELECT * FROM workflow_run ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def usage(self, run_id: int | None = None) -> dict:
        where, args = ("WHERE run_id = ?", (run_id,)) if run_id is not None else ("", ())
        row = self.store.query(
            f"""SELECT count(*) AS calls, sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed,
                       coalesce(sum(num_turns), 0) AS num_turns, coalesce(sum(input_tokens), 0) AS input_tokens,
                       coalesce(sum(output_tokens), 0) AS output_tokens, sum(reported_cost_usd) AS reported_cost_usd
                FROM agent_execution {where}""",
            args,
        )[0]
        row["failed"] = row["failed"] or 0
        return row

    def status_text(self) -> str:
        state = self.store.load()
        return render_status(
            state.project,
            state.tasks,
            state.approvals,
            list(state.decisions.values()),
            self.store.history(),
            self.validate(),
            run=self.latest_run(),
        )

    def report_text(self, run_id: int | None = None) -> str:
        state = self.store.load()
        return render_report(state.project, state.tasks, state.approvals, list(state.decisions.values()), self.validate(), usage=self.usage(run_id))
