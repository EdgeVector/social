# Social

Social is the command-line address book and sharing settings for LastDB. It
moved here from `EdgeVector/lastdb` (`apps/social`) on 2026-11-16.

- `src/social` is the entrypoint. `src/cli.py` holds the command grammar,
  `src/commands.py` maps each command to an owner operation.
- `ci/` holds the DEV sharing recipes run by `dev-sharing.yml`.
- `lastdb-app.json` is the app manifest and the source of Social's private
  schema definitions.
- `setups/shared-albums-v1/` holds the schema definitions of the
  `shared-albums` version 1 setup. They must match `spec/social/` in
  `EdgeVector/lastdb`.

## Rules

- No tests. Pull requests get syntax, manifest and `--help` checks. The
  sharing flow is proven on main by `.github/workflows/dev-sharing.yml`
  (`social-share`, `social-circles`, `social-prepare`). It runs on every
  push to main and by manual dispatch, never on a pull request. The recipes
  are in `ci/`. They start two fresh LastDB nodes from a published LastDB
  nightly, connect them to the DEV cloud, and drive this commit's `social`
  command. A change that alters command output or behaviour must update
  the recipes in the same PR.
- `ci/` imports LastDB's generic node helpers (`support`, `node`,
  `programs`, `harness`, `node_auth`) from a checkout of `EdgeVector/lastdb`
  at the nightly's source commit. Do not copy them here. `ci/social_sdk.mjs`
  exercises the Social adapter of the LastDB app SDK built from that same
  checkout, so a renamed command needs the SDK change in a LastDB nightly
  before these runs pass.
- Preserve whole-database sharing: two logs, the signed per-database roster,
  schema write grants, and the publisher approving each mutation. Read
  [the sharing design](https://github.com/EdgeVector/lastdb/blob/main/docs/designs/shared-database-mutation-logs.md#requirements-to-preserve)
  before changing that path.
- LastDB access is by key and batched. No scan, no loop of one call per record.
- No migration code and no readers for old formats.
- Source file: 400 lines. Function: 100 lines.
- No browser interface. Every owner action is a command.
