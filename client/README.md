# Social client for JavaScript apps

`@lastdb/social-client` lets a JavaScript or TypeScript app use Social. It is a
thin wrapper: each method runs the installed `social` command, reads the JSON
it prints, checks its shape, and returns it. It has no dependencies and no
build step.

It ships inside Social's source, so it always matches the Social it sits next
to. With Social installed from the app registry, the package is the `client`
folder of the installed source: `<apps root>/social-graph/current/source/client`.
Import it from there, or copy the folder into your app.

Social manages verified contacts, reusable circles, and invitations. For
schemas, records, and files, use `connect` and `select` from `@lastdb/app-sdk`.
Point both at the same LastDB home. The publisher needs a connected ExeMem
account.

| Method | Command it runs |
| --- | --- |
| `listCircles()` | `social circle list` |
| `requestSharing(request)` | `social request submit`, with the request on standard input |
| `getSharingStatus(requestId)` | `social request status ID` |
| `createSharedDatabase(name)` | `social db create NAME` |
| `setupDatabase(request)` | `social db create --setup ...` or `social db verify ...` |
| `sealInvitation(databaseId, nodeKey)` | `social invite DATABASE PERSON` |
| `acceptInvitation(invitation, publisherKey, name?)` | `social db accept`, with the invitation on standard input |
| `revokeAccess(database, nodeKey)` | `social grant revoke --apply DATABASE PERSON` |
| `createDatabase(name)` | `social db create --filtered NAME` |
| `inviteReader(database, recipient)` | `social invite --name ... DATABASE PERSON` |

A failed command throws `SocialError` with `code: 'command_failed'` and
Social's own `social: ...` line as the message. Output of an unexpected shape
throws `SocialError` with `code: 'invalid_response'`. A database result is the
row Social prints, which is LastDB's own database row; `id` is always present.

## Share an app's own database

Create an empty named whole-database share when your app owns the schemas.
Save the stable database ID and declare the app's schemas with the selected
LastDB client. For standard album and comment schemas, use the setup below.

```ts
import { join } from 'node:path';
import { connect } from '@lastdb/app-sdk';
import { socialClient } from '@lastdb/social-client';

const social = socialClient({ dataDir: publisherHome });
const root = await connect({
  appId: 'photo-journal',
  socketPath: join(publisherHome, 'data', 'folddb.sock'),
});
const database = await social.createSharedDatabase('family-trips');
const trips = root.select(database.id);
await trips.declareSchema(tripSchema);
```

The owner runs Social on the publisher's device to add verified contacts and
link a circle to the database, for example
`social circle set Family --members Ana --link family-trips=read`. The command
prints the resulting access. Adding `--apply` makes the change. Use
`social db show ID` to see the database's roster. After Social confirms the
grant, the app can get a sealed receipt with
`await social.sealInvitation(database.id, anaVerifiedKey)`. The caller delivers
the receipt to Ana. Keep sealed receipts out of logs.

On Ana's device, use a Social client pointed at her home. Pass the publisher
key verified independently of the receipt, and save the returned database ID.

```ts
const recipientSocial = socialClient({ dataDir: recipientHome });
const received = await recipientSocial.acceptInvitation(receipt, verifiedPublisherKey, 'family-trips');
const recipient = await connect({
  appId: 'photo-journal',
  socketPath: join(recipientHome, 'data', 'folddb.sock'),
});

// Wait for this ID to be ready in listDatabases() before reading its content.
const { client: sharedTrips } = await recipient.openDatabaseSession(received.id, 'read');
const trip = await sharedTrips.get('photo-journal/Trip', tripKey);
```

Use the same ID when another app opens this share on Ana's device. The owner
can manage access in Social or revoke a recipient with
`await social.revokeAccess(database.id, anaVerifiedKey)`.

## Propose circle sharing

Choose a circle from `await social.listCircles()` and submit a declaration.
Use one stable UUID for the request and its retries.

```ts
import { randomUUID } from 'node:crypto';

const request = await social.requestSharing({
  requestId: randomUUID(),
  appId: 'photo-journal',
  change: {
    operation: 'setCircleBinding',
    databaseId: database.id,
    circleId: family.id,
    preset: 'commenter',
  },
});
```

The owner reviews the proposal with `social request show <requestId>` and
decides with `social request approve <requestId>` or
`social request decline <requestId>`, using the same LastDB home. The app can
fetch the result with `social.getSharingStatus(request.requestId)`. Status is
`awaitingApproval`, `pending`, `needsReview`, `confirmed`, or `declined`.
Retrying the same request UUID returns that outcome. When confirmed, use
`sealInvitation` for a new recipient. Use `writeSchemas` in place of a preset
to suggest the app's canonical schema names. A pending grant update blocks
further changes to its database. Distinct databases can proceed with their own
owner review.

The row `acceptInvitation` returns, and `listDatabases` in the app SDK, expose
the receiving node's `selfAccess` when available. An initial response can be
unknown while access is confirmed. An active grant with `stale: false` can
guide which write actions the app displays. The publisher still checks each
proposal.

## Use a named database setup

Social can provision **shared-albums** version 1 with the standard
`social/Album`, `social/AlbumItem`, `social/Photo`, and `social/Comment`
schemas. The owner chooses a local name for provisioning. Other apps open the
selected database by its stable ID and get the verified definitions before
using the ordinary record APIs.

```ts
const created = await social.setupDatabase({
  setupName: 'shared-albums', requiredVersion: 1, localName: 'family',
});
const verified = await social.setupDatabase({
  setupName: 'shared-albums', requiredVersion: 1, databaseId: created.databaseId,
});
const albums = root.select(verified.databaseId);
albums.rememberDefinition('social/Album', verified.definitions['social/Album']!);
```

Verification by ID also works on a received share once it has synced its
schemas. Setup version 1 requires the catalog's schema shapes and leaves
schema upgrades to an explicit owner operation. Use the same circle and
invitation flow as for an app-owned share.

A LastDB build with retryable whole-database creation resumes interrupted
provisioning by the original local name. An owner can choose an existing
incomplete setup by its ID and pass `finishExisting: true` to install missing
schemas after checking compatibility.

## Existing filtered shares

`social.createDatabase(name)` uses the original filtered Social format.
`social.inviteReader(database, { name, nodeKey, contactPath })` grants reader
access and returns a sealed invitation. Social reuses verified contacts, so
later reader invitations can omit `contactPath`. Use `sealInvitation` when the
recipient already has a circle grant on a whole-database share.
