/** Use the same LastDB home for Social and the content client. */
export interface SocialClientOptions {
  dataDir: string;
  /** Installed Social command or explicit executable path. Defaults to `social`. */
  socialPath?: string;
  /** LastDB executable to use when it is outside the caller's PATH. */
  lastdbPath?: string;
}

/** A failed Social command or output of the wrong shape. */
export class SocialError extends Error {
  constructor(code: SocialError['code'], message: string);
  code: 'command_failed' | 'invalid_response';
}

/**
 * The database row Social prints: LastDB's own row, returned as given.
 * Name `id` as `database` on each LastDB read and write item. LastDB limits
 * the app by the schemas it declared, not by database.
 */
export interface SocialDatabase {
  /** Stable database id for the `database` field of `POST /v1/read` and `POST /v1/write`. */
  id: string;
  name?: string | null;
  kind?: string | null;
  sharingMode?: 'whole_database';
  spec?: { name: string; version: number } | null;
  state?: string;
  [field: string]: unknown;
}

/** A sealed invitation for the caller to hand to the verified recipient. */
export interface SocialInvitation {
  databaseId: string;
  revision: string;
  publisher: string;
  invitation: string;
}

/** Provision by a new local name or verify an explicitly selected database. */
export type SocialSetupRequest = {
  setupName: string;
  requiredVersion?: number;
} & (
  { localName: string; databaseId?: never; finishExisting?: never }
  | { databaseId: string; localName?: never; finishExisting?: true }
);

export interface SocialDatabaseSetup {
  databaseId: string;
  name: string | null;
  setupName: string;
  version: number;
  /** Verified installed definition IDs keyed by canonical schema name. */
  definitions: Record<string, string>;
}

type SocialGrantScope =
  { writeSchemas: string[] | 'all'; preset?: never }
  | { preset: 'viewer' | 'commenter' | 'album-organizer'; writeSchemas?: never };

export type SocialSharingChange =
  | ({ operation: 'setCircleBinding'; databaseId: string; circleId: string } & SocialGrantScope)
  | { operation: 'removeCircleBinding'; databaseId: string; circleId: string }
  | ({ operation: 'setPersonGrant' | 'restorePerson'; databaseId: string; nodeKey: string } & SocialGrantScope)
  | { operation: 'revokePerson'; databaseId: string; nodeKey: string };

export interface SocialSharingRequest {
  requestId: string;
  /** Display label for the app that proposed the change. */
  appId: string;
  change: SocialSharingChange;
}

export interface SocialSharingStatus {
  requestId: string;
  databaseId: string;
  status: 'awaitingApproval' | 'pending' | 'confirmed' | 'needsReview' | 'declined';
  revision?: string;
  error?: string;
}

export interface SocialClient {
  /** List the owner's reusable circle labels and stable IDs. */
  listCircles(): Promise<{ id: string; name: string }[]>;
  /** Save a declaration for the owner to approve with `social request approve`. Retry with the same UUID and change. */
  requestSharing(request: SocialSharingRequest): Promise<SocialSharingStatus>;
  /** Read the outcome using the UUID assigned when the app proposed the change. */
  getSharingStatus(requestId: string): Promise<SocialSharingStatus>;
  /** Create a named database using Social's original filtered sharing format. */
  createDatabase(name: string): Promise<SocialDatabase>;
  /** Create an empty named whole-database share for an app's own schemas. */
  createSharedDatabase(name: string): Promise<SocialDatabase>;
  /** Open a versioned database setup using Social's catalog. */
  setupDatabase(request: SocialSetupRequest): Promise<SocialDatabaseSetup>;
  /** Omit contactPath when Social already remembers the verified recipient. */
  inviteReader(database: string, recipient: {
    name: string;
    nodeKey: string;
    contactPath?: string;
  }): Promise<SocialInvitation>;
  /** Seal an invitation for a verified contact with an existing whole-database grant. */
  sealInvitation(databaseId: string, nodeKey: string): Promise<SocialInvitation>;
  /** Accept using the independently verified publisher key. */
  acceptInvitation(invitation: SocialInvitation, publisherKey: string, name?: string): Promise<SocialDatabase>;
  /** Revoke a person's grant and return the applied revision. */
  revokeAccess(database: string, nodeKey: string): Promise<string>;
}

/** Share with people through Social. Use `connect` from `@lastdb/app-sdk` for content. */
export function socialClient(options: SocialClientOptions): SocialClient;
