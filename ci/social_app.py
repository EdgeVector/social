"""Run Social's owner commands and selected-database reads in DEV."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess

from node import Connection
from support import OperationError, decode_json, require, retry, summary


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setups/shared-albums-v1"
SPEC = {"name": "social", "version": 1}
GROUPS = ("person", "circle", "db", "grant", "request")


def lastdb_source():
    """The EdgeVector/lastdb checkout at the source commit of the build under test."""
    path = os.environ.get("LASTDB_SOURCE")
    require(bool(path) and Path(path, "lastdb_node/environments.json").is_file(), "lastdb_source_missing")
    return Path(path)


def command(node):
    # This checkout is the Social under test, unless a signed install replaced it.
    entrypoint = node.programs.environment.get("SOCIAL_COMMAND") or str(ROOT / "src/social")
    return [entrypoint, "--data-dir", str(node.home), "--lastdb", str(node.programs.directory / "lastdb")]


def sdk(node, action, **body):
    # The Social client is this checkout's. LASTDB_APP_SDK holds the packed LastDB data client.
    require(Path(node.programs.environment.get("LASTDB_APP_SDK", ""), "dist/index.js").is_file(),
            "lastdb_app_sdk_missing")
    result = subprocess.run(
        ["node", str(ROOT / "ci/social_sdk.mjs"), action, *command(node)],
        input=json.dumps(body).encode(), capture_output=True, env=node.programs.environment, timeout=190,
    )
    require(result.returncode == 0, f"social_sdk_{action}")
    return decode_json(result.stdout)


def install(publisher, root, revision):
    programs = publisher.programs
    require(len(revision) == 40 and all(char in "0123456789abcdef" for char in revision), "social_install_revision")
    home = root / "os-home"
    home.mkdir(mode=0o700)
    extra = {"HOME": str(home), "LASTDB_APPS_ROOT": str(root / "apps"),
             "PATH": f"{programs.directory}:{os.environ['PATH']}"}
    catalog = programs.environment.get("SOCIAL_INSTALL_REGISTRY") or "dev"
    require(catalog in ("dev", "prod"), "social_install_registry")
    profile = decode_json((lastdb_source() / "lastdb_node/environments.json").read_bytes())
    index = profile["environments"][catalog]["registry_index"]
    receipt = programs.command(publisher.home, [
        "app", "install", "social-graph", "--channel", "next", "--index", index, "--accept-install", "--json",
    ], extra=extra, timeout=150)
    require(isinstance(receipt, list) and len(receipt) == 1 and receipt[0]["sha"] == revision,
            "social_install_exact_source")
    link = home / ".local/bin/social"
    managed = root / "apps/social-graph/current/source/src/social"
    require(link.is_symlink() and link.resolve(strict=True) == managed.resolve(strict=True), "social_install_command")
    programs.environment["SOCIAL_COMMAND"] = str(link)
    summary(f"Installed Social from the signed {catalog.upper()} registry at `{revision}` and used its managed command link.")


def social(node, *arguments, stdin=None, refusal=None, refusals=()):
    """Run one Social command and decode its JSON result."""
    label = "_".join(arguments[:2] if arguments[0] in GROUPS else arguments[:1]).replace("-", "_")
    try:
        result = subprocess.run([*command(node), *arguments],
                                input=None if stdin is None else json.dumps(stdin).encode(),
                                capture_output=True, env=node.programs.environment, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise OperationError(f"social_{label}_deadline") from error
    if refusal is not None:
        require(result.returncode == 1 and not result.stdout and refusal in result.stderr,
                f"social_{label}_expected_refusal")
        return None
    if result.returncode:
        # An invitation is held in memory and must never enter workflow output.
        for fragment, code in refusals:
            require(fragment.encode() not in result.stderr, code)
        detail = result.stderr.decode(errors="replace").partition(" returned HTTP ")[2][:3]
        suffix = f"_http_{detail}" if detail.isascii() and detail.isdigit() else ""
        raise OperationError(f"social_{label}_failed{suffix}")
    return decode_json(result.stdout)


def pick(node, spec, intent, database):
    """Check one database selection. The result is its id, name and spec."""
    return social(node, "pick-database", "--spec", spec["name"], "--version", str(spec["version"]),
                  "--intent", intent, "--database", database)


def content(node, app, method, path, body=None, status=200):
    """One owner-socket call. The caller names a database in the body when the route takes one."""
    headers = {"X-LastDB-Client": app, "Content-Type": "application/json"}
    with closing(Connection(node.socket)) as connection:
        connection.request(method, path, None if body is None else json.dumps(body).encode(), headers)
        with connection.getresponse() as reply:
            require(reply.status == status, f"social_content_{app}_http_{reply.status}")
            return decode_json(reply.read(65537)) if status == 200 else None


def read_named(node, app, schema, key, database=None):
    """Read one record. A database id is a field of the read item."""
    item = {"schema": schema, "key": {"hash": key}}
    if database is not None:
        item["database"] = database
    reply = content(node, app, "POST", "/v1/read", {"reads": [item]})
    require(isinstance(reply.get("results"), list) and len(reply["results"]) == 1,
            f"social_content_{app}_read")
    return reply["results"][0]


def record_key(key):
    """A hash key, plus a range when the record has one."""
    if isinstance(key, str):
        return {"hash": key}
    body = {"hash": key["hash"]}
    if key.get("range"):
        body["range"] = key["range"]
    return body


def read_records(node, database, schema, keys):
    """Keyed reads of one schema. POST /v1/read names the database on each item."""
    reads = [{"database": database, "schema": schema, "key": record_key(key)} for key in keys]
    pages = node.call("/v1/read", {"reads": reads}).get("results")
    require(isinstance(pages, list) and len(pages) == len(keys), "record_read_pages")
    rows = []
    for page in pages:
        records = page.get("records") if isinstance(page, dict) and "error" not in page else None
        require(isinstance(records, list), "record_read_page")
        for record in records:
            fields = record.get("record") if isinstance(record, dict) else None
            revision = record.get("revision") if isinstance(record, dict) else None
            require(isinstance(fields, dict) and isinstance(revision, str) and revision, "record_read_row")
            rows.append({"key": record.get("key"), "revision": revision, "fields": fields,
                         "file": record.get("file")})
    return rows


def write_owned(node, database, *updates):
    """Write records in a database this node owns.

    POST /v1/write takes op, key, record, and revision. It has no definition
    id, source file name, or metadata. A received database is not this route:
    a proposal is POST /api/databases/{id}/submissions.
    """
    mutations = [owned_item(database, update) for update in updates]
    reply = node.call("/v1/write", {"mutations": mutations})
    revisions = reply.get("revisions")
    require(reply.get("written") == len(mutations) and isinstance(revisions, list)
            and len(revisions) == len(mutations)
            and all(isinstance(item, str) and item for item in revisions), "owned_write_receipt")
    return revisions


def owned_item(database, update):
    op = update["mutation_type"]
    item = {"database": database, "schema": update["schema"], "op": op,
            "key": record_key(update["key_value"])}
    if op != "delete":
        item["record"] = update["record"]
        item["alternates"] = update.get("alternates") or {}
    if op != "create":
        item["revision"] = update["expected"]["revision"]
    return item


def open_content_apps(recipient, database):
    choices = social(recipient, "pick-database", "--spec", SPEC["name"], "--version", str(SPEC["version"]),
                     "--intent", "read")
    require(any(row["databaseId"] == database for row in choices), "social_picker_received_database")
    selections = [(name, pick(recipient, SPEC, "read", database)) for name in ("album-viewer", "photo-journal")]
    require(all(selection["databaseId"] == database and selection["spec"] == SPEC
                for _, selection in selections), "social_picker_shared_database")
    refused = {"database": database, "schema": "social/Album", "op": "create",
               "key": {"hash": "album-refused"}, "record": {"title": "refused"}}
    for app, _selection in selections:
        # The personal database is the read that omits database. It is not this share.
        personal = read_named(recipient, app, "social/Album", "album-0")
        require("records" not in personal or personal["records"] == [], f"social_content_{app}_personal")
        # The roster is an owner route. A received database is not this node's share.
        content(recipient, app, "GET", f"/api/databases/{database}/share/config", status=404)
        # A received database is read-only on the app write route.
        content(recipient, app, "POST", "/v1/write", {"mutations": [refused]}, status=403)
    return selections


def album_records(page):
    records = page.get("records") if isinstance(page, dict) else None
    return records if isinstance(records, list) else None


def read_album(node, selections, database, key, title=None):
    with ThreadPoolExecutor(max_workers=2) as pool:
        pages = list(pool.map(
            lambda item: read_named(node, item[0], "social/Album", key, database), selections))
    if title is None:
        return all(album_records(page) == [] for page in pages)
    return all(album_records(page) and len(album_records(page)) == 1
               and album_records(page)[0].get("record", {}).get("title") == title
               for page in pages)


def observe_access(node, database, state, revision=None, *, role="read", write_schemas=None):
    def matches(result):
        access = result["database"]["selfAccess"]
        return (access["state"] == state and access["confirmedAtMs"] and not access["stale"]
                and (revision is None or access["revision"] == str(revision))
                and (state != "active" or access["role"] == role)
                and (write_schemas is None or access.get("writeSchemas") == write_schemas))
    retry(f"social_recipient_access_{state}", lambda: social(node, "db", "show", database), matches)


def report_content_flow():
    message = ("Two content clients selected the same database, opened the same albums, and were refused "
               "on personal data, the roster, and reader writes. Both retained cached albums after revoke "
               "and caught up after regrant.")
    print(message, flush=True)
    summary(message)
