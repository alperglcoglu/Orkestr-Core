-- Orkestr core schema, version 1 (PHASE3_IMPLEMENTATION.md §5.2).
-- Field meanings and enums are the Phase 1 ones (PHASE1_IMPLEMENTATION.md §5).

CREATE TABLE project (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  schema_version  INTEGER NOT NULL CHECK (schema_version = 1),
  name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
  goal            TEXT NOT NULL CHECK (length(trim(goal)) > 0),
  status          TEXT NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED', 'CANCELLED', 'COMPLETED')),
  status_reason   TEXT,
  autonomy        TEXT CHECK (autonomy IS NULL OR autonomy IN ('bounded', 'supervised')),
  managed_by      TEXT NOT NULL CHECK (managed_by IN ('plugin', 'core')),
  limits_json     TEXT NOT NULL,
  counters_json   TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  CHECK (status NOT IN ('PAUSED', 'CANCELLED') OR length(trim(coalesce(status_reason, ''))) > 0)
);

CREATE TABLE project_list_item (
  kind      TEXT NOT NULL CHECK (kind IN ('constraint', 'success_criterion')),
  position  INTEGER NOT NULL,
  text      TEXT NOT NULL,
  PRIMARY KEY (kind, position)
);

CREATE TABLE assumption (
  id        INTEGER PRIMARY KEY,
  text      TEXT NOT NULL,
  impact    TEXT CHECK (impact IS NULL OR impact IN ('low', 'medium', 'high')),
  task_id   TEXT,
  at        TEXT NOT NULL
);

CREATE TABLE task (
  id                     TEXT PRIMARY KEY CHECK (id GLOB 'T-[0-9][0-9][0-9]*'),
  seq                    INTEGER NOT NULL,
  title                  TEXT NOT NULL CHECK (length(trim(title)) > 0),
  description            TEXT NOT NULL DEFAULT '',
  owner                  TEXT NOT NULL,
  status                 TEXT NOT NULL CHECK (status IN ('BACKLOG', 'READY', 'IN_PROGRESS', 'BLOCKED', 'REVIEW', 'FAILED', 'COMPLETED', 'CANCELLED')),
  requires_approval      INTEGER NOT NULL CHECK (requires_approval IN (0, 1)),
  approval_id            TEXT,
  authorized             INTEGER NOT NULL CHECK (authorized IN (0, 1)),
  source_type            TEXT NOT NULL CHECK (source_type IN ('plan', 'meeting', 'manual')),
  source_ref             TEXT,
  decision_id            TEXT,
  blocked_reason         TEXT,
  blocked_by_dependency  TEXT,
  failure_json           TEXT,
  fix_attempts           INTEGER NOT NULL CHECK (fix_attempts >= 0),
  recovery_meetings      INTEGER NOT NULL DEFAULT 0 CHECK (recovery_meetings >= 0),
  verify_json            TEXT,
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL,
  CHECK (status <> 'BLOCKED' OR length(trim(coalesce(blocked_reason, ''))) > 0),
  CHECK (status <> 'FAILED' OR failure_json IS NOT NULL),
  CHECK (status NOT IN ('READY', 'IN_PROGRESS', 'REVIEW', 'FAILED', 'COMPLETED') OR authorized = 1)
);

CREATE TABLE task_dependency (
  task_id        TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  depends_on_id  TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  position       INTEGER NOT NULL,
  PRIMARY KEY (task_id, depends_on_id),
  CHECK (task_id <> depends_on_id)
);
CREATE INDEX task_dependency_depends_on ON task_dependency(depends_on_id);

CREATE TABLE task_scope (
  task_id   TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  position  INTEGER NOT NULL,
  pattern   TEXT NOT NULL,
  PRIMARY KEY (task_id, position)
);

CREATE TABLE task_acceptance_criterion (
  task_id   TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  position  INTEGER NOT NULL,
  text      TEXT NOT NULL,
  PRIMARY KEY (task_id, position)
);

CREATE TABLE task_note (
  task_id   TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  position  INTEGER NOT NULL,
  author    TEXT NOT NULL,
  text      TEXT NOT NULL,
  at        TEXT NOT NULL,
  PRIMARY KEY (task_id, position)
);

CREATE TABLE test_result (
  id           INTEGER PRIMARY KEY,
  task_id      TEXT REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  command      TEXT NOT NULL,
  exit_code    INTEGER,
  duration_ms  INTEGER NOT NULL,
  timed_out    INTEGER NOT NULL DEFAULT 0 CHECK (timed_out IN (0, 1)),
  output_tail  TEXT NOT NULL DEFAULT '',
  run_by       TEXT NOT NULL,
  at           TEXT NOT NULL
);
CREATE INDEX test_result_task ON test_result(task_id);

CREATE TABLE evidence (
  task_id         TEXT NOT NULL REFERENCES task(id) DEFERRABLE INITIALLY DEFERRED,
  position        INTEGER NOT NULL,
  type            TEXT NOT NULL CHECK (type IN ('test', 'check', 'review', 'manual')),
  result          TEXT NOT NULL CHECK (result IN ('passed', 'failed', 'not_run')),
  command         TEXT,
  summary         TEXT NOT NULL,
  ref             TEXT,
  test_result_id  INTEGER REFERENCES test_result(id) DEFERRABLE INITIALLY DEFERRED,
  at              TEXT NOT NULL,
  PRIMARY KEY (task_id, position)
);

CREATE TABLE approval (
  id                TEXT PRIMARY KEY CHECK (id GLOB 'A-[0-9][0-9][0-9]*'),
  action            TEXT NOT NULL,
  category          TEXT NOT NULL CHECK (category IN ('destructive', 'deploy', 'publish', 'git_push', 'external_communication', 'spending', 'permission_change', 'sensitive_data', 'other')),
  reason            TEXT NOT NULL,
  consequences      TEXT NOT NULL DEFAULT '',
  risks             TEXT NOT NULL DEFAULT '',
  requested_by      TEXT NOT NULL,
  task_id           TEXT,
  status            TEXT NOT NULL CHECK (status IN ('PENDING', 'GRANTED', 'REJECTED', 'EXPIRED', 'CANCELLED')),
  decided_by        TEXT,
  decided_at        TEXT,
  decision_note     TEXT,
  execution_result  TEXT,
  created_at        TEXT NOT NULL,
  CHECK (status = 'PENDING' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
  CHECK (execution_result IS NULL OR status = 'GRANTED')
);

CREATE TABLE meeting (
  id            TEXT PRIMARY KEY CHECK (id GLOB 'M-[0-9][0-9][0-9]*'),
  date          TEXT NOT NULL,
  objective     TEXT NOT NULL,
  context       TEXT NOT NULL,
  trigger       TEXT NOT NULL,
  outcome       TEXT NOT NULL CHECK (outcome IN ('DECIDED', 'ESCALATED', 'NO_DECISION')),
  decision_id   TEXT,
  recovery_for  TEXT,
  body_json     TEXT NOT NULL
);

CREATE TABLE meeting_participant (
  meeting_id      TEXT NOT NULL REFERENCES meeting(id) DEFERRABLE INITIALLY DEFERRED,
  role            TEXT NOT NULL,
  responsibility  TEXT NOT NULL,
  PRIMARY KEY (meeting_id, role)
);

CREATE TABLE meeting_contribution (
  meeting_id          TEXT NOT NULL REFERENCES meeting(id) DEFERRABLE INITIALLY DEFERRED,
  round               INTEGER NOT NULL CHECK (round >= 1),
  role                TEXT NOT NULL,
  position            TEXT NOT NULL,
  key_points_json     TEXT NOT NULL DEFAULT '[]',
  concerns_json       TEXT NOT NULL DEFAULT '[]',
  agent_execution_id  INTEGER,
  PRIMARY KEY (meeting_id, round, role)
);

CREATE TABLE decision (
  id               TEXT PRIMARY KEY CHECK (id GLOB 'D-[0-9][0-9][0-9]*'),
  date             TEXT NOT NULL,
  question         TEXT NOT NULL,
  selected_option  TEXT,
  approval_status  TEXT NOT NULL CHECK (approval_status IN ('PROPOSED', 'NEEDS_FOUNDER', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
  founder_required INTEGER NOT NULL DEFAULT 0 CHECK (founder_required IN (0, 1)),
  meeting_id       TEXT,
  recovery_for     TEXT,
  decided_by       TEXT,
  decided_at       TEXT,
  note             TEXT,
  body_json        TEXT NOT NULL,
  CHECK (approval_status <> 'APPROVED' OR selected_option IS NOT NULL)
);

CREATE TABLE decision_task (
  decision_id  TEXT NOT NULL REFERENCES decision(id) DEFERRABLE INITIALLY DEFERRED,
  task_id      TEXT NOT NULL,
  relation     TEXT NOT NULL CHECK (relation IN ('created', 'applies_to')),
  PRIMARY KEY (decision_id, task_id, relation)
);

CREATE TABLE review (
  id                  TEXT PRIMARY KEY CHECK (id GLOB 'R-[0-9][0-9][0-9]*'),
  date                TEXT NOT NULL,
  task_id             TEXT,
  verdict             TEXT NOT NULL CHECK (verdict IN ('PASS', 'CHANGES_REQUESTED', 'BLOCKED')),
  scope               TEXT NOT NULL,
  reviewers_json      TEXT NOT NULL,
  assumptions_json    TEXT NOT NULL DEFAULT '[]',
  agent_execution_id  INTEGER,
  body_json           TEXT NOT NULL
);

CREATE TABLE review_finding (
  review_id       TEXT NOT NULL REFERENCES review(id) DEFERRABLE INITIALLY DEFERRED,
  position        INTEGER NOT NULL,
  title           TEXT NOT NULL,
  severity        TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
  status          TEXT NOT NULL CHECK (status IN ('confirmed', 'suspected')),
  component       TEXT,
  evidence        TEXT NOT NULL,
  recommendation  TEXT,
  PRIMARY KEY (review_id, position)
);

CREATE TABLE retro (
  id         TEXT PRIMARY KEY CHECK (id GLOB 'RT-[0-9][0-9][0-9]*'),
  date       TEXT NOT NULL,
  scope      TEXT NOT NULL,
  body_json  TEXT NOT NULL
);

CREATE TABLE artifact (
  path        TEXT PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('prd', 'architecture', 'lessons', 'report', 'other')),
  sha256      TEXT NOT NULL,
  updated_by  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE agent_definition (
  role         TEXT PRIMARY KEY,
  source_path  TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  max_turns    INTEGER,
  tools_json   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE workflow_run (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('run', 'plan', 'build', 'review', 'meeting', 'retro')),
  goal           TEXT,
  status         TEXT NOT NULL CHECK (status IN ('RUNNING', 'PAUSED', 'WAITING_FOUNDER', 'COMPLETED', 'FAILED', 'CANCELLED')),
  status_reason  TEXT,
  control        TEXT CHECK (control IS NULL OR control IN ('pause', 'cancel')),
  agent_calls    INTEGER NOT NULL DEFAULT 0 CHECK (agent_calls >= 0),
  options_json   TEXT NOT NULL DEFAULT '{}',
  report_path    TEXT,
  started_at     TEXT NOT NULL,
  ended_at       TEXT
);

CREATE TABLE workflow_step (
  id          INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES workflow_run(id),
  seq         INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  task_id     TEXT,
  status      TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED')),
  attempt     INTEGER NOT NULL DEFAULT 1,
  input_json  TEXT NOT NULL DEFAULT '{}',
  result_json TEXT,
  error       TEXT,
  started_at  TEXT,
  ended_at    TEXT,
  UNIQUE (run_id, seq)
);

CREATE TABLE agent_execution (
  id                 INTEGER PRIMARY KEY,
  run_id             INTEGER REFERENCES workflow_run(id),
  step_id            INTEGER REFERENCES workflow_step(id),
  role               TEXT NOT NULL,
  task_id            TEXT,
  executor           TEXT NOT NULL,
  model              TEXT,
  status             TEXT NOT NULL CHECK (status IN ('ok', 'invalid_output', 'error', 'usage_limit', 'timeout', 'cancelled')),
  started_at         TEXT NOT NULL,
  ended_at           TEXT NOT NULL,
  duration_ms        INTEGER NOT NULL,
  num_turns          INTEGER,
  input_tokens       INTEGER,
  output_tokens      INTEGER,
  reported_cost_usd  REAL,
  session_id         TEXT,
  error              TEXT,
  output_json        TEXT
);
CREATE INDEX agent_execution_run ON agent_execution(run_id);
CREATE INDEX agent_execution_task ON agent_execution(task_id);

CREATE TABLE audit_event (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  type        TEXT NOT NULL,
  actor       TEXT,
  entity      TEXT,
  from_state  TEXT,
  to_state    TEXT,
  summary     TEXT NOT NULL DEFAULT '',
  data_json   TEXT,
  run_id      INTEGER
);

-- The audit log is append-only (MASTER_SPEC §14).
CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;

-- Hashes of the Phase 1 files at the last import or export (PHASE3_IMPLEMENTATION.md §5.1).
CREATE TABLE file_sync (
  path       TEXT PRIMARY KEY,
  sha256     TEXT NOT NULL,
  synced_at  TEXT NOT NULL
);

-- Only a PENDING approval may change, and only once (MASTER_SPEC §10.3). A GRANTED approval
-- may receive its execution result once. Approvals are never deleted.
CREATE TRIGGER approval_closed_no_update BEFORE UPDATE ON approval
WHEN OLD.status <> 'PENDING' AND (
  NEW.status IS NOT OLD.status OR NEW.decided_by IS NOT OLD.decided_by OR NEW.decided_at IS NOT OLD.decided_at
  OR NEW.decision_note IS NOT OLD.decision_note OR NEW.action IS NOT OLD.action OR NEW.category IS NOT OLD.category
  OR NEW.reason IS NOT OLD.reason OR NEW.task_id IS NOT OLD.task_id
  OR (OLD.execution_result IS NOT NULL AND NEW.execution_result IS NOT OLD.execution_result)
)
BEGIN SELECT RAISE(ABORT, 'only PENDING approvals can change'); END;
CREATE TRIGGER approval_no_delete BEFORE DELETE ON approval
BEGIN SELECT RAISE(ABORT, 'approvals are never deleted'); END;
