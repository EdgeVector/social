"""Prepare an unsigned Social DEV registry declaration for this commit using a CI node.

The same node then reads the signed DEV `next` list for its own LastDB API
version. A registry listing is tied to one exact API version, so a LastDB API
change leaves Social uninstallable on the new build until someone republishes
it. This check fails the run when that listing is missing.
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
from support import OperationError, failure, require, running, summary

sys.path.insert(0, str(ROOT / "src"))
from app import SocialApp  # noqa: E402
from lastdb import LastDB  # noqa: E402

APP_ID = "social-graph"


def prepare():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REF") == "refs/heads/main", "social_prepare_accepted_ci_only")
    programs = Programs(args.bin_dir.resolve(strict=True), args.version, dict(os.environ))
    with tempfile.TemporaryDirectory(prefix="ldb-social-", dir="/tmp") as temporary:
        root = Path(temporary).resolve()
        home = root / "node"
        initialized = programs.command(None, [
            "admin", "backup", "init", "--into", str(home), "--recovery-phrase-out",
            str(home / "recovery-phrase.txt"), "--json",
        ], timeout=20)
        node = OwnedNode(home, programs, account=initialized["account_id"])
        declaration = root / "declaration.json"
        with running(node):
            lastdb = LastDB(home, programs.directory / "lastdb")
            app = SocialApp(lastdb)
            app.home()
            app.sharing.pending_edit("registry-preflight")
            origin = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
            expected_origin = "https://github.com/EdgeVector/social.git"
            require(origin in (expected_origin, expected_origin.removesuffix(".git")), "social_prepare_origin")
            subprocess.run(["git", "remote", "set-url", "origin", expected_origin], cwd=ROOT, check=True)
            subprocess.run([
                str(programs.directory / "lastdb"), "--data-dir", str(home), "app", "publish", "--env", "dev",
                "--channel", "next", "--manifest", str(ROOT / "lastdb-app.json"), "--prepare", str(declaration),
            ], check=True, timeout=60)
            # Anonymous read of the signed list at this node's API version. No credential.
            listed = subprocess.run([
                str(programs.directory / "lastdb"), "--data-dir", str(home), "app", "list", APP_ID,
                "--env", "dev", "--channel", "next", "--json",
            ], capture_output=True, text=True, timeout=60)
        payload = json.loads(declaration.read_text(encoding="utf-8"))
        require(payload["sha"] == os.environ["GITHUB_SHA"] and payload["app_id"] == APP_ID
                and payload["source"] == expected_origin and payload["channel"] == "next"
                and payload["lastdb_build"] == args.version,
                "social_prepare_exact_source")
    statement = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    print(f"SOCIAL_DECLARATION {statement}", flush=True)
    summary(f"Unsigned DEV registry declaration\n\n`{statement}`")
    require_listing(listed, payload, expected_origin)


def require_listing(listed, payload, source):
    """Fail when the DEV `next` list has no Social entry for this node's API version."""
    api_version = payload["api_version"]
    if listed.returncode != 0:
        # The command names the list URL or the missing app. It holds no secret.
        reason = (listed.stderr.strip().splitlines() or ["no output"])[-1][:300]
        summary(f"No readable DEV `next` listing of Social for LastDB API {api_version}: `{reason}`.\n\n"
                "If the listing is missing, install and upgrade refuse on this LastDB build until Social "
                "is republished (brain `sop-publish-social-to-dev-registry`). An installed copy keeps running.")
        # A missing list file answers 403 or 404; a list without Social names the app.
        missing = "no app '" in reason or "returned 403" in reason or "returned 404" in reason
        raise OperationError("social_dev_listing_missing_for_api" if missing else "social_dev_listing_read_failed")
    entry = json.loads(listed.stdout)
    require(entry.get("app_id") == APP_ID and entry.get("source") == source
            and entry.get("api_version") == api_version and entry.get("status") == "listed",
            "social_dev_listing_mismatch")
    behind = ("" if entry.get("sha") == payload["sha"]
              else " This commit is not the listed one; publishing is a separate manual step.")
    summary(f"DEV `next` listing for LastDB API {api_version}: Social {entry.get('app_version')} "
            f"at `{entry.get('sha')}`.{behind}")


if __name__ == "__main__":
    try:
        prepare()
    except OperationError as error:
        print(json.dumps(failure(error)), flush=True)
        raise SystemExit(1) from None
