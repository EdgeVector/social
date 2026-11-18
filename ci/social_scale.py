"""Hold more members in one shared database than a signed member list could.

Until 2026-11-17 the publisher signed the whole member set as one document.
That capped a shared database near 1,500 writers or 2,600 readers, and every
member change moved all of it. The publisher now signs one entry per member.

This recipe shares a database with one real recipient, then adds 3,000
generated reader keys through the publisher node's own configuration route.
It checks that the recipient still receives new records, that changing one
member costs about the same at 10 members and at 3,000, and that a revoke
still stops the real recipient.
"""

import json
import os
import sys
import time
import uuid

from social_app import ROOT, SETUP, observe_access, sdk, social
from social_share import album, wait_for_album, write_album
from support import OperationError, require, retry, run_operation, summary

sys.path.insert(0, str(ROOT / "src"))
from lastdb import LastDB, LastDBError  # noqa: E402

SMALL = 10
MEMBERS = 3000
STEP = 500


def owner(node):
    return LastDB(node.home, node.programs.directory / "lastdb")


def configuration(node, database):
    try:
        reply = owner(node).call("GET", f"/api/databases/{database}/share/config")
    except LastDBError as error:
        # The route answers 503 until the node has confirmed its cloud state.
        raise OperationError("social_scale_configuration_unavailable") from error
    require(reply["databaseId"] == database, "social_scale_configuration")
    return reply


def add_readers(node, database, keys):
    """Name every stored member again plus `keys`, as the route requires."""
    current = configuration(node, database)
    members = current["members"] + [{"nodeKey": key, "role": "read", "state": "active"} for key in keys]
    request = {"expectedRevision": current["revision"], "requestId": str(uuid.uuid4()), "members": members}
    ack = owner(node).call("PUT", f"/api/databases/{database}/share/config", request)
    require(ack["databaseId"] == database and ack["requestId"] == request["requestId"]
            and ack["committed"] is True and int(ack["revision"]) > int(current["revision"]),
            "social_scale_configuration_ack")


def timed_revoke(node, database, key):
    """Seconds for one member change: one entry read and one entry write."""
    started = time.monotonic()
    ack = owner(node).call("POST", f"/api/databases/{database}/share/members",
                           {"action": "revoke", "recipientKey": key}, expect_ok=False)
    elapsed = time.monotonic() - started
    require(ack.get("database_id") == database and ack.get("unchanged") is False, "social_scale_revoke_ack")
    return elapsed


def scale(programs, root, publisher, recipient, api_key):
    name = f"ci-scale-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    created = sdk(publisher, "create", name=name)
    database = str(uuid.UUID(created["id"]))
    summary(f"Owned DEV mutation log `{database}` for `{name}`.")
    print(f"Social DEV scale database {database}", flush=True)
    schema = json.loads((SETUP / "Album.json").read_text())
    publisher.call(f"/api/databases/{database}/apps/declare-schema", schema)
    write_album(publisher, database, 0)
    publisher.stop()
    connected = programs.command(publisher.home, [
        "backup", "connect", "--existing", "--env", "dev", "--api-key-stdin",
        "--expected-user-hash", publisher.account, "--json",
    ], input_bytes=api_key, timeout=20)
    require(connected["environment"] == "dev" and connected["credentials_saved"] is True,
            "social_scale_dev_connection")
    publisher.keep_saved_credentials = True
    publisher.start()
    contact = root / "reader.contact"
    reader_key = programs.command(recipient.home, [
        "share", "contact", "--out", str(contact),
    ])["recipient_public_key"]
    publisher_key = programs.command(publisher.home, [
        "share", "contact", "--out", str(root / "publisher.contact"),
    ])["recipient_public_key"]
    retry("social_scale_initial_configuration", lambda: configuration(publisher, database),
          lambda config: config["revision"] == "0" and config["members"] == [])
    social(publisher, "person", "add", "Ana", "--node-key", reader_key, "--contact", str(contact))
    invited = revoked = False
    try:
        receipt = sdk(publisher, "invite", database=name, recipient={"name": "Ana", "nodeKey": reader_key})
        invited = True
        received = sdk(recipient, "accept", publisher=publisher_key, name="scale", receipt=receipt)
        require(received.get("id") == database and received.get("access") == "reader",
                "social_scale_received")
        wait_for_album(recipient, database, 0)

        generated = [os.urandom(32).hex() for _ in range(MEMBERS)]
        require(len(set(generated)) == MEMBERS and reader_key not in generated
                and publisher_key not in generated, "social_scale_generated_keys")
        add_readers(publisher, database, generated[:SMALL])
        small_seconds = timed_revoke(publisher, database, generated[0])

        for start in range(SMALL, MEMBERS, STEP):
            add_readers(publisher, database, generated[start:start + STEP])
        stored = configuration(publisher, database)["members"]
        require(len(stored) == MEMBERS + 1
                and {member["nodeKey"] for member in stored} == {reader_key, *generated},
                "social_scale_member_count")

        large_seconds = timed_revoke(publisher, database, generated[1])
        # One member change reads and writes one entry, so it does not grow
        # with the member set. The margin covers ordinary network variation.
        require(large_seconds < small_seconds * 5 + 5, "social_scale_member_change_grew")

        write_album(publisher, database, 1)
        wait_for_album(recipient, database, 1)
        observe_access(recipient, database, "active")

        timed_revoke(publisher, database, reader_key)
        revoked = True
        write_album(publisher, database, 2)
        for _ in range(12):
            require(album(recipient, database, 2) == [], "social_scale_revoked_read_new_album")
            time.sleep(1)
        observe_access(recipient, database, "revoked")
    finally:
        if invited and not revoked:
            owner(publisher).call("POST", f"/api/databases/{database}/share/members",
                                  {"action": "revoke", "recipientKey": reader_key}, expect_ok=False)
    message = (f"One shared database held {MEMBERS + 1} members. The recipient received a record written "
               f"after they were added and stopped receiving after its revoke. One member change took "
               f"{small_seconds:.2f} s at {SMALL + 1} members and {large_seconds:.2f} s at {MEMBERS + 1}.")
    print(message, flush=True)
    summary(message)


if __name__ == "__main__":
    run_operation(scale)
