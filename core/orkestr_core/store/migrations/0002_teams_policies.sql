-- Orkestr core schema, version 2: agent teams and permission policies
-- (PHASE4_IMPLEMENTATION.md §5, §6, §8). Forward-only, like 0001.

CREATE TABLE team_role (
  role          TEXT PRIMARY KEY CHECK (role IN (
                  'product-manager', 'system-architect', 'tech-lead', 'backend-engineer', 'frontend-engineer',
                  'ml-engineer', 'data-engineer', 'qa-engineer', 'security-reviewer', 'researcher')),
  enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  model         TEXT CHECK (model IS NULL OR (length(model) BETWEEN 1 AND 64 AND substr(model, 1, 1) <> '-')),
  max_turns     INTEGER CHECK (max_turns IS NULL OR max_turns >= 1),
  instructions  TEXT CHECK (instructions IS NULL OR length(instructions) <= 2000),
  updated_at    TEXT NOT NULL,
  -- Required roles (domain/team.py REQUIRED_ROLES) are always enabled.
  CHECK (enabled = 1 OR role NOT IN ('product-manager', 'tech-lead', 'qa-engineer', 'security-reviewer'))
);

INSERT INTO team_role (role, updated_at)
SELECT value, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM json_each(
  '["product-manager","system-architect","tech-lead","backend-engineer","frontend-engineer","ml-engineer","data-engineer","qa-engineer","security-reviewer","researcher"]'
);

CREATE TRIGGER team_role_no_delete BEFORE DELETE ON team_role
BEGIN SELECT RAISE(ABORT, 'team roles are never deleted; disable them instead'); END;

CREATE TABLE policy_version (
  version      INTEGER PRIMARY KEY CHECK (version >= 0),
  policy_json  TEXT NOT NULL CHECK (json_valid(policy_json) AND json_type(policy_json) = 'object'),
  reason       TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  created_at   TEXT NOT NULL,
  created_by   TEXT NOT NULL
);

CREATE TRIGGER policy_version_in_order BEFORE INSERT ON policy_version
WHEN NEW.version <> (SELECT coalesce(max(version), -1) + 1 FROM policy_version)
BEGIN SELECT RAISE(ABORT, 'policy versions are numbered in order'); END;
CREATE TRIGGER policy_version_no_update BEFORE UPDATE ON policy_version
BEGIN SELECT RAISE(ABORT, 'policy_version is append-only'); END;
CREATE TRIGGER policy_version_no_delete BEFORE DELETE ON policy_version
BEGIN SELECT RAISE(ABORT, 'policy_version is append-only'); END;

INSERT INTO policy_version VALUES (
  0,
  '{"test_commands":[],"protected_paths":[],"approval_paths":[],"security_review_paths":[],"limits":{}}',
  'default policy',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
  'orkestr'
);

ALTER TABLE workflow_run ADD COLUMN policy_version INTEGER REFERENCES policy_version(version);
