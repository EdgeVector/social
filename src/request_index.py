"""Keyed list of sharing requests awaiting an owner decision."""

import json

from lastdb import LastDBError, record_mutation


INDEX = "social-graph/PendingSharingRequest"
REQUESTS = "social-graph/SharingRequest"
HASH = "awaiting"


def index_key(request_id):
    return {"hash": HASH, "range": request_id}


class RequestIndex:
    def __init__(self, node, reusable):
        self.node, self.reusable = node, reusable

    def add(self, request):
        fields = {"request_id": request["requestId"], "app_id": request["appId"],
                  "database_id": request["change"]["databaseId"]}
        return record_mutation(INDEX, index_key(request["requestId"]), fields, None)

    def get(self, request_id):
        return self.node.read_keys(INDEX, [index_key(request_id)])[0]

    def remove(self, request_id):
        row = self.get(request_id)
        return record_mutation(INDEX, index_key(request_id), None, row) if row else None

    def awaiting(self, cursor):
        page = self.node.read_range(INDEX, HASH, limit=64, cursor=cursor)
        rows = {row["fields"]["request_id"]: row for row in page["records"]}
        pending, decided = self.read_pending(rows)
        self.prune(rows, decided)
        return {
            "requests": [{"requestId": request["requestId"], "appId": request["appId"],
                          "databaseId": request["change"]["databaseId"]} for request in pending],
            "nextCursor": page["next_cursor"],
        }

    def prune(self, rows, decided):
        if decided:
            try:
                self.node.write([
                    record_mutation(INDEX, index_key(key), None, rows[key]) for key in decided
                ])
            except LastDBError as error:
                if error.status != 409:
                    raise
                # Another Social command may have cleaned up these entries first.

    def read_pending(self, request_ids):
        if not request_ids:
            return [], []
        # Read operations first. A newer operation saves the previous decision
        # to its request before replacing the database's update record.
        operations = self.reusable.load().operations
        records = self.node.read_keys(REQUESTS, list(request_ids))
        pending, decided = [], []
        for row in records:
            if not row:
                continue
            saved = json.loads(row["fields"]["request"])
            request = saved["declaration"]
            operation = operations.get(request["change"]["databaseId"])
            if "receipt" in saved or (operation and operation["requestId"] == request["requestId"]):
                decided.append(request["requestId"])
            else:
                pending.append(request)
        return pending, decided
