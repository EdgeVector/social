// Social client for JavaScript apps: a thin wrapper that runs the installed
// `social` command and checks the JSON it prints. It has no dependencies and
// no build step. It ships with Social, so it matches Social's commands.

import { execFile } from 'node:child_process';

/** A failed Social command (`command_failed`) or output of the wrong shape (`invalid_response`). */
export class SocialError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'SocialError';
    this.code = code;
  }
}

const invalid = (message) => new SocialError('invalid_response', message);
const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const STATUSES = ['awaitingApproval', 'pending', 'needsReview', 'confirmed', 'declined'];

/** Share with people through Social. Use `connect` from `@lastdb/app-sdk` for content. */
export function socialClient({ dataDir, socialPath = 'social', lastdbPath }) {
  const baseArgs = ['--data-dir', dataDir];
  if (lastdbPath !== undefined) baseArgs.push('--lastdb', lastdbPath);

  function run(command, args, input) {
    const action = command.join(' ');
    return new Promise((resolve, reject) => {
      const child = execFile(socialPath, [...baseArgs, ...command, ...args], {
        timeout: 180_000,
        maxBuffer: 1024 * 1024,
      }, (error, stdout, stderr) => {
        if (error) {
          const detail = stderr.split('\n').find((line) => line.startsWith('social: '));
          reject(new SocialError('command_failed',
            detail ?? `Social ${action} failed (${error.code ?? 'interrupted'})`));
          return;
        }
        try {
          resolve(JSON.parse(stdout));
        } catch {
          reject(invalid(`Social ${action} returned invalid JSON`));
        }
      });
      // A child can reject its arguments and close stdin before reading.
      child.stdin?.on('error', (error) => {
        if (error.code !== 'EPIPE') {
          reject(new SocialError('command_failed', `Could not send input to Social ${action}`));
        }
      });
      child.stdin?.end(input === undefined ? undefined : JSON.stringify(input));
    });
  }

  return {
    /** List the owner's reusable circle labels and stable IDs. */
    async listCircles() {
      const value = await run(['circle', 'list'], []);
      if (!Array.isArray(value) || !value.every((item) =>
        isObject(item) && typeof item.id === 'string' && typeof item.name === 'string')) {
        throw invalid('Social returned an invalid circle list');
      }
      return value.map(({ id, name }) => ({ id, name }));
    },

    /** Save a declaration for the owner to approve with `social request approve`. Retry with the same UUID and change. */
    async requestSharing(request) {
      const receipt = parseSharingStatus(await run(['request', 'submit'], [], request), request.requestId);
      if (receipt.databaseId !== request.change.databaseId) {
        throw invalid('Social returned sharing status for a different database');
      }
      return receipt;
    },

    /** Read the outcome using the UUID assigned when the app proposed the change. */
    async getSharingStatus(requestId) {
      return parseSharingStatus(await run(['request', 'status'], ['--', requestId]), requestId);
    },

    /** Create a named database using Social's original filtered sharing format. */
    async createDatabase(name) {
      return parseDatabase(await run(['db', 'create'], ['--filtered', '--', name]));
    },

    /** Create an empty named whole-database share for an app's own schemas. */
    async createSharedDatabase(name) {
      const database = parseDatabase(await run(['db', 'create'], ['--', name]));
      if (database.sharingMode !== 'whole_database') {
        throw invalid('Social did not create a whole-database share');
      }
      return database;
    },

    /** Open a versioned database setup using Social's catalog. */
    async setupDatabase(request) {
      const args = [`--setup=${request.setupName}`, `--version=${request.requiredVersion ?? 1}`];
      if (request.localName !== undefined) {
        return parseSetup(await run(['db', 'create'], [...args, '--', request.localName]), request);
      }
      if (request.finishExisting) args.push('--finish');
      return parseSetup(await run(['db', 'verify'], [...args, '--', request.databaseId]), request);
    },

    /** Omit contactPath when Social already remembers the verified recipient. */
    async inviteReader(database, recipient) {
      const args = [`--name=${recipient.name}`];
      if (recipient.contactPath !== undefined) args.push(`--contact=${recipient.contactPath}`);
      return parseInvitation(await run(['invite'], [...args, '--', database, recipient.nodeKey]));
    },

    /** Seal an invitation for a verified contact with an existing whole-database grant. */
    async sealInvitation(databaseId, nodeKey) {
      const result = parseInvitation(await run(['invite'], ['--', databaseId, nodeKey]));
      if (result.databaseId !== databaseId) {
        throw invalid('Social returned an invitation for a different database');
      }
      return result;
    },

    /** Accept using the independently verified publisher key. */
    async acceptInvitation(invitation, publisherKey, name) {
      const args = [`--publisher=${publisherKey}`];
      if (name !== undefined) args.push(`--name=${name}`);
      const database = parseDatabase(await run(['db', 'accept'], args, invitation));
      if (database.id !== invitation.databaseId) {
        throw invalid('Social accepted a different database');
      }
      return database;
    },

    /** Revoke a person's grant and return the applied revision. */
    async revokeAccess(database, nodeKey) {
      const result = await run(['grant', 'revoke'], ['--apply', '--', database, nodeKey]);
      if (!isObject(result) || typeof result.revision !== 'string' || result.state !== 'revoked'
        || result.nodeKey !== nodeKey || typeof result.databaseId !== 'string') {
        throw invalid('Social returned an invalid revoke receipt');
      }
      return result.revision;
    },
  };
}

/** The database row Social prints. It is LastDB's own row, returned as given. */
function parseDatabase(value) {
  if (isObject(value) && typeof value.id === 'string' && value.id.length > 0) return value;
  throw invalid('Social returned an invalid database');
}

function parseSharingStatus(value, expectedId) {
  if (isObject(value)) {
    const { requestId, databaseId, status, revision, error } = value;
    if (requestId === expectedId && typeof databaseId === 'string' && STATUSES.includes(status)
      && (revision === undefined || typeof revision === 'string')
      && (error === undefined || typeof error === 'string')
      && (status !== 'confirmed' || typeof revision === 'string')) {
      return { requestId, databaseId, status, ...(revision === undefined ? {} : { revision }),
        ...(error === undefined ? {} : { error }) };
    }
  }
  throw invalid('Social returned invalid sharing status');
}

function parseInvitation(result) {
  if (isObject(result)) {
    const { databaseId, revision, publisher, invitation } = result;
    if (typeof databaseId === 'string' && typeof revision === 'string'
      && typeof publisher === 'string' && typeof invitation === 'string' && invitation.length > 0) {
      return { databaseId, revision, publisher, invitation };
    }
  }
  throw invalid('Social returned an invalid invitation receipt');
}

function parseSetup(result, request) {
  if (isObject(result)) {
    const { databaseId, name, setupName, version, definitions } = result;
    if (typeof databaseId === 'string' && databaseId.length > 0
      && (request.databaseId === undefined || databaseId === request.databaseId)
      && (name === null || typeof name === 'string')
      && setupName === request.setupName && version === (request.requiredVersion ?? 1)
      && isObject(definitions)) {
      const ids = Object.values(definitions);
      if (ids.length > 0 && ids.every((id) => typeof id === 'string' && /^[0-9a-f]{64}$/.test(id))) {
        return { databaseId, name, setupName, version, definitions };
      }
    }
  }
  throw invalid('Social returned an invalid database setup');
}
