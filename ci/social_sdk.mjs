import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { socialClient } from '../client/index.mjs';

const [action, socialPath, , dataDir, , lastdbPath] = process.argv.slice(2);
const social = socialClient({ dataDir, socialPath, lastdbPath });
const input = JSON.parse(readFileSync(0, 'utf8'));

// Content reads use the LastDB app SDK built from the source of the build under test.
async function connect(options) {
  const sdk = await import(pathToFileURL(join(process.env.LASTDB_APP_SDK, 'dist', 'index.js')).href);
  return sdk.connect(options);
}

try {
  let result;
  switch (action) {
    case 'create':
      result = await social.createDatabase(input.name);
      break;
    case 'create-shared':
      result = await social.createSharedDatabase(input.name);
      break;
    case 'setup':
      result = await social.setupDatabase(input);
      break;
    case 'circles':
      result = await social.listCircles();
      break;
    case 'request-sharing':
      result = await social.requestSharing(input);
      break;
    case 'sharing-status':
      result = await social.getSharingStatus(input.requestId);
      break;
    case 'invite':
      result = await social.inviteReader(input.database, input.recipient);
      break;
    case 'seal':
      result = await social.sealInvitation(input.database, input.nodeKey);
      break;
    case 'accept':
      result = await social.acceptInvitation(input.receipt, input.publisher, input.name);
      break;
    case 'revoke':
      result = await social.revokeAccess(input.database, input.nodeKey);
      break;
    case 'read': {
      const root = await connect({ appId: input.appId ?? 'social-dev-sdk', socketPath: join(dataDir, 'data', 'folddb.sock') });
      const [page] = await root.read([{
        database: input.database,
        schema: input.schema,
        key: { hash: input.key },
      }]);
      if (!page || page.error || !Array.isArray(page.records) || page.records.length !== 1) {
        throw new Error('read failed');
      }
      const row = page.records[0];
      result = { fields: row.record, revision: row.revision, key: row.key };
      break;
    }
    case 'list-databases': {
      const root = await connect({ appId: 'social-dev-sdk', socketPath: join(dataDir, 'data', 'folddb.sock') });
      result = await root.listDatabases();
      break;
    }
    case 'submission-status': {
      const root = await connect({ appId: 'social-dev-sdk', socketPath: join(dataDir, 'data', 'folddb.sock') });
      const client = root.select(input.database);
      result = await Promise.all(input.ids.map(id => client.getShareSubmissionStatus(id)));
      break;
    }
    default:
      throw new Error('Unknown SDK operation');
  }
  process.stdout.write(JSON.stringify(result));
} catch {
  // Keep invitation material and captured replies off the workflow output.
  process.exitCode = 1;
}
