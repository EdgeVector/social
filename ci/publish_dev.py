"""List this commit of Social in the DEV registry, from a fresh node.

A registry listing is tied to one exact LastDB API version, so Social has to
be listed again after each LastDB API change. `lastdb app publish` takes the
API version from the node it talks to, and it refuses a node that holds an
app schema the manifest does not list. A node that ran an older Social holds
such schemas. This command therefore starts a new, empty node from the given
LastDB programs, places this checkout's schemas the way the installer does,
and publishes against that node. The node and its home are removed after.

Run it by hand from a clean checkout of the commit to list. Without
`--publish` it only validates the declaration and prints it. With `--publish`
it reads the publisher's person key from standard input and the DEV account
key from `EXEMEM_DEV_API_KEY`, the same way `lastdb app publish` does:

    <print the person key> | EXEMEM_DEV_API_KEY=<key> PYTHONPATH=ci/lib \
        python3 ci/publish_dev.py --bin-dir <dir> --version <version> --publish

`--bin-dir` holds `lastdb` and `lastdbd` as real files, not symlinks. Their
API version is the one the listing gets.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from node import OwnedNode, Programs
from social_app import ROOT
from support import OperationError, failure, require, running

sys.path.insert(0, str(ROOT / "src"))
from app import SocialApp  # noqa: E402
from lastdb import LastDB  # noqa: E402

APP_ID = "social-graph"
SOURCE = "https://github.com/EdgeVector/social.git"
FIELDS = ("app_id", "app_version", "sha", "api_version", "lastdb_build", "channel", "source")


def publish():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    person_key = b""
    if args.publish:
        require(bool(os.environ.get("EXEMEM_DEV_API_KEY")), "publish_dev_account_key_missing")
        person_key = sys.stdin.buffer.read()
        require(bool(person_key.strip()), "publish_dev_person_key_missing")
    origin = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    require(origin == SOURCE, "publish_dev_origin")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    # The node never needs the account key. Keep it out of the daemon's environment.
    node_environment = {name: value for name, value in os.environ.items() if name != "EXEMEM_DEV_API_KEY"}
    programs = Programs(args.bin_dir.resolve(strict=True), args.version, node_environment)
    lastdb_program = str(programs.directory / "lastdb")
    # A Unix socket path is short. Keep the home directly under /tmp.
    with tempfile.TemporaryDirectory(prefix="ldbs-", dir="/tmp") as temporary:
        root = Path(temporary).resolve()
        home = root / "node"
        initialized = programs.command(None, [
            "admin", "backup", "init", "--into", str(home), "--recovery-phrase-out",
            str(home / "recovery-phrase.txt"), "--json",
        ], timeout=20)
        node = OwnedNode(home, programs, account=initialized["account_id"])
        declaration = root / "declaration.json"
        command = [lastdb_program, "--data-dir", str(home), "app", "publish", "--env", "dev",
                   "--channel", "next", "--manifest", str(ROOT / "lastdb-app.json")]
        with running(node):
            lastdb = LastDB(home, programs.directory / "lastdb")
            app = SocialApp(lastdb)
            app.home()
            app.sharing.pending_edit("registry-preflight")
            subprocess.run([*command, "--prepare", str(declaration)], check=True, timeout=60,
                           env=node_environment, stdout=subprocess.DEVNULL)
            payload = json.loads(declaration.read_text(encoding="utf-8"))
            require(payload["sha"] == head and payload["app_id"] == APP_ID and payload["source"] == SOURCE
                    and payload["channel"] == "next" and payload["lastdb_build"] == args.version,
                    "publish_dev_exact_source")
            result = {"declaration": {name: payload.get(name) for name in FIELDS}, "published": False}
            if args.publish:
                done = subprocess.run([*command, "--key-file", "-"], input=person_key, capture_output=True,
                                      timeout=120)
                if done.returncode != 0:
                    # The command's last line names the refusal. It holds no key.
                    reason = (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["no output"])[-1]
                    print(json.dumps({**result, "refused": reason[:300]}), flush=True)
                    raise OperationError("publish_dev_refused")
                result["published"] = True
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    try:
        publish()
    except OperationError as error:
        print(json.dumps(failure(error)), flush=True)
        raise SystemExit(1) from None
