# Social

Social is the command-line address book and sharing settings for LastDB. It
moved here from `EdgeVector/lastdb` (`apps/social`) on 2026-11-16.

- `src/social` is the entrypoint. `src/cli.py` holds the command grammar,
  `src/commands.py` maps each command to an owner operation.
- `lastdb-app.json` is the app manifest and the source of Social's private
  schema definitions.
- `setups/shared-albums-v1/` holds the schema definitions of the
  `shared-albums` version 1 setup. They must match `spec/social/` in
  `EdgeVector/lastdb`.

## Rules

- No tests. Use syntax, manifest and `--help` checks. The sharing flow is
  proven by the `social-share` and `social-circles` DEV runs in
  `EdgeVector/lastdb` (`.github/workflows/cloud-backup-recovery.yml`). Those
  runs clone this repo at the commit in `scripts/cloud_backup/social.rev`
  there. After a change here that the runs should cover, open a lastdb PR
  that moves that pin and dispatch the runs on main.
- Preserve whole-database sharing: two logs, the signed per-database roster,
  schema write grants, and the publisher approving each mutation. Read
  [the sharing design](https://github.com/EdgeVector/lastdb/blob/main/docs/designs/shared-database-mutation-logs.md#requirements-to-preserve)
  before changing that path.
- LastDB access is by key and batched. No scan, no loop of one call per record.
- No migration code and no readers for old formats.
- Source file: 400 lines. Function: 100 lines.
- No browser interface. Every owner action is a command.
