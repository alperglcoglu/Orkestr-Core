"""`orkestr-core` command (PHASE3_IMPLEMENTATION.md §9.1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .domain.errors import OrkestrError
from .service import Project


def _executor(name: str):
    if name == "claude":
        from .executor.claude_cli import ClaudeCliExecutor

        return ClaudeCliExecutor()
    raise OrkestrError(f"Unknown executor: {name}", "E_USAGE")


def _engine(project: Project, args):
    from .workflow.engine import Engine

    return Engine(project, _executor(args.executor), model=args.model, max_agent_calls=args.max_agent_calls, log=lambda m: print(f"[orkestr] {m}", flush=True))


def _decide(project: Project, target: str, approve: bool, option: str | None, note: str | None) -> str:
    if target.upper() == "PLAN":
        if not approve:
            raise OrkestrError("Plans are not rejected; cancel or change the tasks instead.", "E_USAGE")
        ids = project.approve_plan(note)
        return f"Plan approved; authorized {', '.join(ids)}."
    if target.startswith("A-"):
        a = project.decide_approval(target, "grant" if approve else "reject", note)
        return f"{a['id']} is now {a['status']}."
    if target.startswith("D-"):
        r = project.decide_decision(target, "approve" if approve else "reject", option=option, note=note)
        return f"{target} is now {r['decision']['approval_status']}."
    raise OrkestrError("Pass an approval (A-###), a decision (D-###), or `plan`.", "E_USAGE")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orkestr-core", description="Orkestr standalone orchestration core")
    p.add_argument("--project", default=".", help="project directory (default: current directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project managed by the core")
    s.add_argument("--name", required=True)
    s.add_argument("--goal", required=True)
    s.add_argument("--constraint", action="append", default=[])
    s.add_argument("--success", action="append", default=[])
    s.add_argument("--autonomy", default="bounded", choices=["bounded", "supervised"])

    sub.add_parser("import", help="hand a Phase 1 (plugin) project to the core")
    s = sub.add_parser("export", help="write the Phase 1 mirror files")
    s.add_argument("--release", action="store_true", help="hand the project back to the plugin")
    s.add_argument("--force", action="store_true", help="overwrite mirror files changed outside the core")

    for name, help_text in (("run", "run the workflow until done or a founder decision is needed"), ("resume", "resume a paused project and continue its run")):
        s = sub.add_parser(name, help=help_text)
        if name == "run":
            s.add_argument("goal", nargs="?", help="a new request; without it the open run continues")
        s.add_argument("--executor", default="claude", choices=["claude"])
        s.add_argument("--model", default=None)
        s.add_argument("--max-agent-calls", type=int, default=None)

    s = sub.add_parser("retro", help="run one retrospective agent call over the project history")
    s.add_argument("--scope", default=None, help="what the retrospective covers (default: the project goal)")
    s.add_argument("--executor", default="claude", choices=["claude"])
    s.add_argument("--model", default=None)
    s.add_argument("--max-agent-calls", type=int, default=None)

    s = sub.add_parser("pause", help="pause after the current step")
    s.add_argument("--reason", default=None)
    s = sub.add_parser("cancel", help="cancel the open run (or, with --project-cancel, the whole project)")
    s.add_argument("--reason", default=None)
    s.add_argument("--project-cancel", action="store_true", help="cancel the whole project (founder only; cannot be undone)")

    sub.add_parser("status", help="status report")
    sub.add_parser("report", help="completion report")
    sub.add_parser("validate", help="validate the stored state")

    for name in ("approve", "reject"):
        s = sub.add_parser(name, help=f"{name} an approval (A-###), a decision (D-###), or the plan")
        s.add_argument("target")
        s.add_argument("--option", default=None, help="decision option id (approve only)")
        s.add_argument("--reason", "--note", dest="note", default=None)
    s = sub.add_parser("authorize", help="authorize a task (founder)")
    s.add_argument("task")
    s = sub.add_parser("retry", help="retry a FAILED task (founder; resets fix attempts)")
    s.add_argument("task")
    s = sub.add_parser("approval-result", help="record the result of a granted action you carried out")
    s.add_argument("approval")
    s.add_argument("--result", required=True)

    s = sub.add_parser("team", help="show or change the agent team (applies when a run starts or continues)")
    team_sub = s.add_subparsers(dest="team_cmd", required=True)
    team_sub.add_parser("show", help="each role with its settings")
    t = team_sub.add_parser("set", help="change one role (founder)")
    t.add_argument("role")
    on_off = t.add_mutually_exclusive_group()
    on_off.add_argument("--enable", dest="enabled", action="store_true", default=None)
    on_off.add_argument("--disable", dest="enabled", action="store_false")
    t.add_argument("--model", default=None, help='model alias or name; "" clears it')
    t.add_argument("--max-turns", type=int, default=None, help="lower turn limit; 0 clears it")
    t.add_argument("--instructions-file", default=None, help="extra instructions for this role (UTF-8 text); an empty file clears them")

    s = sub.add_parser("policy", help="show or change the permission policy (applies when a run starts or continues)")
    policy_sub = s.add_subparsers(dest="policy_cmd", required=True)
    policy_sub.add_parser("show", help="the current policy as JSON")
    policy_sub.add_parser("history", help="every policy version")
    t = policy_sub.add_parser("set", help="save a new policy version from a JSON file (founder)")
    t.add_argument("file")
    t.add_argument("--reason", required=True)

    s = sub.add_parser("autonomy", help="change the autonomy mode (founder)")
    s.add_argument("mode", choices=["bounded", "supervised"])
    s.add_argument("--reason", required=True)

    s = sub.add_parser("serve", help="start the local HTTP API on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s = sub.add_parser("dashboard", help="start the local API and open the founder dashboard in the browser")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true", help="print the dashboard address instead of opening a browser")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    project = Project(Path(args.project))
    try:
        return _dispatch(project, args)
    except OrkestrError as err:
        print(f"orkestr-core: {err.message}", file=sys.stderr)
        return 2 if err.code == "E_USAGE" else 1


def _require_project(project: Project) -> None:
    """Every command after init/import needs a core database. Checking here keeps commands that
    write into .orkestr/ (the run lock, the API token) from failing with a raw traceback."""
    if project.store.exists():
        return
    if (project.dir / "project.json").exists():
        hint = "It holds a plugin project; run `orkestr-core import` to hand it to the core."
    else:
        hint = "Run the command from the project directory, pass --project <dir>, or create one with `orkestr-core init`."
    raise OrkestrError(f"No orkestr-core project in {project.root}. {hint}", "E_NO_PROJECT")


def _dispatch(project: Project, args) -> int:
    cmd = args.cmd
    if cmd == "init":
        project.init(args.name, args.goal, args.constraint, args.success, args.autonomy)
        print(f"Initialized an orkestr-core project in {project.root} (autonomy: {args.autonomy}).")
        return 0
    if cmd == "import":
        r = project.import_phase1()
        print(f"Imported {r['tasks']} task(s) and {r['events']} history event(s); the core now manages this project.")
        if r["backup"]:
            print(f"The previous core database was kept as .orkestr/{r['backup']}.")
        return 0
    _require_project(project)
    if cmd == "export":
        files = project.export(release=args.release, force=args.force)
        print(f"Wrote {len(files)} file(s)." + (" The plugin manages this project again." if args.release else ""))
        return 0
    if cmd in ("run", "resume"):
        from .domain import state as st

        if cmd == "resume" and project.store.load().project["status"] == "PAUSED":
            project.mutate(lambda s: st.set_project_status(s, "resume"))
        run = _engine(project, args).run(getattr(args, "goal", None))
        print(project.report_text(run_id=run["id"]))
        return 0 if run["status"] in ("COMPLETED", "WAITING_FOUNDER", "PAUSED") else 1
    if cmd == "retro":
        run = _engine(project, args).retro(args.scope)
        if run["retro_id"]:
            print((project.dir / "retros" / f"{run['retro_id']}.md").read_text(encoding="utf-8"))
            return 0
        print(f"orkestr-core: retrospective {run['status']}: {run['status_reason']}", file=sys.stderr)
        return 1
    if cmd == "pause":
        from .workflow.engine import request_control

        print(request_control(project, "pause", args.reason))
        return 0
    if cmd == "cancel":
        if args.project_cancel:
            if not args.reason:
                raise OrkestrError("--reason is required to cancel the project.", "E_USAGE")
            project.cancel_project(args.reason)
            print("Project cancelled.")
            return 0
        from .workflow.engine import request_control

        print(request_control(project, "cancel", args.reason))
        return 0
    if cmd == "status":
        print(project.status_text())
        return 0
    if cmd == "report":
        print(project.report_text())
        return 0
    if cmd == "validate":
        v = project.validate()
        for e in v["errors"]:
            print(f"ERROR: {e}")
        for w in v["warnings"]:
            print(f"WARNING: {w}")
        print(f"Validation failed: {len(v['errors'])} error(s)." if v["errors"] else f"Validation passed ({len(v['warnings'])} warning(s)).")
        return 1 if v["errors"] else 0
    if cmd in ("approve", "reject"):
        print(_decide(project, args.target, cmd == "approve", args.option, args.note))
        return 0
    if cmd == "authorize":
        project.authorize_task(args.task)
        print(f"Authorized {args.task}.")
        return 0
    if cmd == "retry":
        project.retry_task(args.task)
        print(f"{args.task} is READY again (fix attempts reset).")
        return 0
    if cmd == "approval-result":
        project.record_approval_result(args.approval, args.result)
        print(f"Recorded the result for {args.approval}.")
        return 0
    if cmd == "team":
        if args.team_cmd == "set":
            change: dict = {}
            if args.enabled is not None:
                change["enabled"] = args.enabled
            if args.model is not None:
                change["model"] = args.model
            if args.max_turns is not None:
                change["max_turns"] = args.max_turns or None
            if args.instructions_file is not None:
                change["instructions"] = Path(args.instructions_file).read_text(encoding="utf-8")
            if not change:
                raise OrkestrError("Nothing to change; pass --enable/--disable, --model, --max-turns, or --instructions-file.", "E_USAGE")
            project.set_team_role(args.role, change)
            print(f"Updated {args.role}; the change applies when a run starts or continues.")
        for role, r in project.team().items():
            extras = [f"model {r['model']}" if r["model"] else "", f"max turns {r['max_turns']}" if r["max_turns"] else "", "instructions" if r["instructions"] else ""]
            print(f"{'on ' if r['enabled'] else 'off'} {role}{'  (' + ', '.join(x for x in extras if x) + ')' if any(extras) else ''}")
        return 0
    if cmd == "policy":
        import json

        if args.policy_cmd == "set":
            try:
                policy = json.loads(Path(args.file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as err:
                raise OrkestrError(f"Could not read the policy file: {err}", "E_USAGE") from None
            version = project.set_policy(policy, args.reason)
            print(f"Saved policy version {version}; it applies when a run starts or continues.")
            return 0
        if args.policy_cmd == "history":
            for v in project.store.policy_versions():
                print(f"v{v['version']}  {v['created_at']}  {v['created_by']}: {v['reason']}")
            return 0
        p = project.policy()
        print(f"Policy version {p['version']} ({p['reason']})")
        print(json.dumps(p["policy"], indent=2))
        return 0
    if cmd == "autonomy":
        project.set_autonomy(args.mode, args.reason)
        print(f"Autonomy is now {args.mode}.")
        return 0
    if cmd == "serve":
        from .api.app import serve

        serve(project, port=args.port)
        return 0
    if cmd == "dashboard":
        from .api.app import dashboard_url, serve

        if args.no_open:
            print(f"Open {dashboard_url(project, args.port)} (the address contains your API token; do not share it).")
        serve(project, port=args.port, open_dashboard=not args.no_open)
        return 0
    raise OrkestrError(f"Unknown command: {cmd}", "E_USAGE")


if __name__ == "__main__":
    sys.exit(main())
