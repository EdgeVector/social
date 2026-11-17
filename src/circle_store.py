"""Keep each reusable circle, and each database's sharing progress, in its own record."""

from __future__ import annotations

import json

from lastdb import SocialError, record_mutation


# Schema name, the hash every record of it shares, and the field that is its range.
CIRCLES = ("Circle", "circles", "circle_id")
UPDATES = ("DatabaseUpdate", "databases", "database_id")


def encoded(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def schema_name(kind):
    return f"social-graph/{kind[0]}"


class Directory:
    """The circles and per-database progress as one read saw them."""

    def __init__(self, circles, updates):
        self.rows = {CIRCLES: circles, UPDATES: updates}
        self.circles = sorted(({"id": key, "name": row["fields"]["name"],
                                "members": json.loads(row["fields"]["members"]),
                                "links": json.loads(row["fields"]["links"])}
                               for key, row in circles.items()), key=lambda circle: circle["id"])
        self.pending = sorted(key for key, row in updates.items() if row["fields"]["pending"])
        operations = {key: json.loads(row["fields"]["operation"]) for key, row in updates.items()}
        self.operations = {key: value for key, value in operations.items() if value is not None}

    def revisions(self):
        return sorted([kind[0], key, row["revision"]] for kind, rows in self.rows.items()
                      for key, row in rows.items())


class CircleStore:
    def __init__(self, node):
        self.node = node

    def load(self):
        for kind in (CIRCLES, UPDATES):
            self.node.declare_private(kind[0])
        rows = {CIRCLES: {}, UPDATES: {}}
        cursors = {CIRCLES: None, UPDATES: None}
        while cursors:
            kinds = list(cursors)
            queries = [{"schema_name": schema_name(kind), "filter": {"HashKey": kind[1]}, "limit": 64,
                        **({"cursor": cursors[kind]} if cursors[kind] else {})} for kind in kinds]
            batch = self.node.call("POST", "/api/queries/batch", {"queries": queries})["results"]
            if len(batch) != len(kinds) or any(item["status"] != 200 for item in batch):
                raise SocialError("LastDB could not read the circles")
            for kind, item in zip(kinds, batch):
                page = item["response"]
                rows[kind].update({row["fields"][kind[2]]: row for row in page["results"]})
                if not page["has_more"]:
                    del cursors[kind]
                elif not page.get("next_cursor"):
                    raise SocialError("LastDB omitted the next circle cursor")
                else:
                    cursors[kind] = page["next_cursor"]
        return Directory(rows[CIRCLES], rows[UPDATES])

    def save(self, directory, circles, pending, operations, extra=None):
        """Write the records that differ from `directory`, with `extra`, as one atomic batch."""
        desired = {
            CIRCLES: {circle["id"]: {"circle_id": circle["id"], "name": circle["name"],
                                     "members": encoded(circle["members"]),
                                     "links": encoded(circle["links"])} for circle in circles},
            UPDATES: {database: {"database_id": database, "pending": database in pending,
                                 "operation": encoded(operations.get(database))}
                      for database in set(pending) | set(operations)},
        }
        mutations, targets = [], []
        for kind, wanted in desired.items():
            definition = self.node.declare_private(kind[0])
            stored = directory.rows[kind]
            for key in sorted(wanted.keys() | stored.keys()):
                fields, previous = wanted.get(key), stored.get(key)
                if previous and previous["fields"] == fields:
                    continue
                mutations.append(record_mutation(schema_name(kind), definition,
                                                 {"hash": kind[1], "range": key}, fields, previous))
                targets.append((kind, key, fields))
        if extra is not None:
            mutations.append(extra)
        if not mutations:
            return directory
        ack = self.node.call("POST", "/api/mutations/batch", {"mutations": mutations})
        rows = {kind: dict(stored) for kind, stored in directory.rows.items()}
        for (kind, key, fields), mutation in zip(targets, ack["mutation_ids"]):
            if fields is None:
                del rows[kind][key]
            else:
                rows[kind][key] = {"fields": fields, "revision": ack["record_revisions"][mutation]}
        return Directory(rows[CIRCLES], rows[UPDATES])
