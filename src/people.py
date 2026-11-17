"""Private Social contacts stored as keyed Person records."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib

from lastdb import LastDBError, SocialError, record_mutation


PERSON_SCHEMA = "social-graph/Person"
ADDRESS_BOOK = "social-graph/AddressBookEntry"
BOOK_HASH = "contacts"


class People:
    def __init__(self, node):
        self.node = node

    def remember(self, node_key, display_name, contact_code):
        person_id = hashlib.sha256(node_key.encode()).hexdigest()
        for _ in range(3):
            existing = self.node.get_record(PERSON_SCHEMA, person_id)
            fields = dict(existing["fields"]) if existing else {
                "person_id": person_id, "node_key": node_key, "avatar": "none",
                "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if fields["node_key"] != node_key:
                raise SocialError("stored contact has a different node key")
            if fields.get("display_name") == display_name and fields.get("contact_code") == contact_code:
                self.index(person_id)
                return fields
            fields.update(display_name=display_name, contact_code=contact_code)
            try:
                self.node.write_record(PERSON_SCHEMA, person_id, fields, existing)
            except LastDBError as error:
                if error.status != 409:
                    raise
            else:
                self.index(person_id)
                return fields
        raise SocialError("contact changed concurrently; retry the operation")

    def remove(self, node_key):
        """Delete the contact and its address book entry in one write."""
        person_id = hashlib.sha256(node_key.encode()).hexdigest()
        entry_key = {"hash": BOOK_HASH, "range": person_id}
        person_page, entry_page = self.node.read([
            {"schema": PERSON_SCHEMA, "key": {"hash": person_id}},
            {"schema": ADDRESS_BOOK, "key": entry_key},
        ])
        person = person_page["records"][:1]
        entry = entry_page["records"][:1]
        if not person:
            raise SocialError("this person is not in the address book")
        removals = [record_mutation(PERSON_SCHEMA, person_id, None, person[0])]
        if entry:
            removals.append(record_mutation(ADDRESS_BOOK, entry_key, None, entry[0]))
        self.node.write(removals)

    def by_keys(self, node_keys):
        if not node_keys:
            return {}
        person_ids = [hashlib.sha256(key.encode()).hexdigest() for key in sorted(set(node_keys))]
        batches = [person_ids[index:index + 64] for index in range(0, len(person_ids), 64)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            return {person["node_key"]: person for batch in pool.map(self.read_batch, batches)
                    for person in batch}

    def read_batch(self, person_ids):
        return [row["fields"] for row in self.node.read_keys(PERSON_SCHEMA, person_ids) if row]

    def index(self, person_id):
        target = {"hash": BOOK_HASH, "range": person_id}
        try:
            self.node.write([record_mutation(ADDRESS_BOOK, target, {"person_id": person_id}, None)])
        except LastDBError as error:
            if error.status != 409:
                raise

    def address_book(self):
        result = []
        cursor = None
        while True:
            page = self.node.read_range(ADDRESS_BOOK, BOOK_HASH, limit=64, cursor=cursor, keys_only=True)
            keys = [key.get("range") if isinstance(key, dict) else None for key in page["keys"]]
            if any(not key for key in keys):
                raise SocialError("LastDB returned an address book key without a person")
            if keys:
                result.extend(self.read_batch(keys))
            cursor = page["next_cursor"]
            if not cursor:
                return result
