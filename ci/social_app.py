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
APP = "social-graph"
RECEIPT = "lastdb-app-install.json"


def lastdb_source():
    """The EdgeVector/lastdb checkout at the source commit of the build under test."""
    path = os.environ.get("LASTDB_SOURCE")
    require(bool(path) and Path(path, "lastdb_node/environments.json").is_file(), "lastdb_source_missing")
    return Path(path)


def apps_dir(node):
    return node.home.parent / "apps"


def prepare_launcher(node):
    """Lay out the launcher tree and register social-graph on this node."""
    apps = apps_dir(node)
    current = apps / APP / "current"
    if not (current / RECEIPT).is_file():
        write_launcher_receipt(current)
    link = apps / "bin" / "social"
    if not link.is_symlink():
        link.parent.mkdir(parents=True, mode=0o700)
        link.symlink_to(node.programs.directory / "lastdb")
    if not getattr(node, "social_registered", False):
        register_social(node, apps)
        node.social_registered = True
    return link


def write_launcher_receipt(current):
    current.mkdir(parents=True, mode=0o700)
    source = current / "source"
    if not source.exists():
        source.symlink_to(ROOT)
    manifest = json.loads((ROOT / "lastdb-app.json").read_text(encoding="utf-8"))
    blank = "0" * 40
    listing = {
        "app_id": APP, "channel": "next", "app_version": manifest["version"],
        "source": manifest["source"], "sha": blank, "api_version": 1, "lastdb_build": "ci",
        "publisher_account": "0" * 32, "publisher_key": "ci", "publisher_sig": "ci",
        "status": "listed", "published_at": "2026-11-18T00:00:00Z",
        "access": {"read": "*", "write": "*", "extensions": ["databases"]},
    }
    receipt = {
        "app_id": APP, "sha": blank, "version": manifest["version"], "channel": "next",
        "index": "ci", "trust": "ci", "publisher": "ci", "consent_hash": "0" * 64,
        "inactive": False, "installed_at": "2026-11-18T00:00:00Z", "listing": listing,
    }
    (current / RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")


def register_social(node, apps):
    manifest_path = apps / APP / "current" / "source" / "lastdb-app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = apps / APP / "data"
    data.mkdir(parents=True, mode=0o700)
    (data / "tmp").mkdir(mode=0o700, exist_ok=True)
    access = manifest["access"]
    reply = node.call("/api/apps/register", {
        "app": APP,
        "access": {"read": access["read"], "write": access["write"], "extensions": access["extensions"]},
        "data": str(data.resolve()),
    })
    require(reply.get("app") == APP, "social_app_register")


def command(node):
    """The `social` command, which is the launcher. It is not src/social itself.

    The launcher reads the node home from LASTDB_HOME before it sees --data-dir.
    A signed install keeps its own receipt. This still registers the app on the
    node that is about to run the command.
    """
    link = prepare_launcher(node)
    lastdb = str(node.programs.directory / "lastdb")
    return [str(link), "--data-dir", str(node.home), "--lastdb", lastdb]


def launcher_env(node):
    env = dict(node.programs.environment)
    env["LASTDB_APPS_ROOT"] = str(apps_dir(node))
    env["LASTDB_HOME"] = str(node.home)
    return env


def sdk(node, action, **body):
    # The Social client is this checkout's. LASTDB_APP_SDK holds the packed LastDB data client.
    require(Path(node.programs.environment.get("LASTDB_APP_SDK", ""), "dist/index.js").is_file(),
            "lastdb_app_sdk_missing")
    result = subprocess.run(
        ["node", str(ROOT / "ci/social_sdk.mjs"), action, *command(node)],
        input=json.dumps(body).encode(), capture_output=True, env=launcher_env(node), timeout=190,
    )
    require(result.returncode == 0, f"social_sdk_{action}")
    return decode_json(result.stdout)


def install(publisher, root, revision):
    programs = publisher.programs
    require(len(revision) == 40 and all(char in "0123456789abcdef" for char in revision), "social_install_revision")
    home = root / "os-home"
    home.mkdir(mode=0o700)
    # The installer points the social command at this lastdb program.
    launcher = home / ".local/bin/lastdb"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not launcher.exists():
        launcher.symlink_to(programs.directory / "lastdb")
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
    # A restricted app's command link points at the lastdb program. Called as
    # `social`, that program is the launcher.
    link = home / ".local/bin/social"
    require(link.is_symlink() and os.readlink(link) == str(launcher), "social_install_command")
    summary(f"Installed Social from the signed {catalog.upper()} registry at `{revision}`. "
            "Recipes start that install through the launcher.")


def social(node, *arguments, stdin=None, refusal=None, refusals=()):
    """Run one Social command and decode its JSON result."""
    label = "_".join(arguments[:2] if arguments[0] in GROUPS else arguments[:1]).replace("-", "_")
    try:
        result = subprocess.run([*command(node), *arguments],
                                input=None if stdin is None else json.dumps(stdin).encode(),
                                capture_output=True, env=launcher_env(node), timeout=180)
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
    id, source file name, or metadata. A received database is not this route.
    A member proposal is the databases action submit. This helper is the owner write.
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
