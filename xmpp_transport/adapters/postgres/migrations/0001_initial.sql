CREATE TABLE xmpp_accounts (
    id UUID PRIMARY KEY,
    bare_jid TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE backend_bindings (
    binding_id TEXT PRIMARY KEY,
    xmpp_account_id UUID NOT NULL REFERENCES xmpp_accounts(id) ON DELETE CASCADE,
    backend_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'authorization_lost', 'disabled')),
    encrypted_credentials BYTEA,
    remote_account_key BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (binding_id, backend_id)
);

-- The key is a stable, non-reversible keyed digest produced by the application.
-- It enforces ownership without storing a remote personal account identifier in clear text.
CREATE UNIQUE INDEX backend_bindings_active_remote_account_uq
    ON backend_bindings (backend_id, remote_account_key)
    WHERE status = 'active' AND remote_account_key IS NOT NULL;

CREATE INDEX backend_bindings_active_idx
    ON backend_bindings (backend_id, binding_id)
    WHERE status = 'active';

CREATE TABLE roster_sync_records (
    binding_id TEXT NOT NULL REFERENCES backend_bindings(binding_id) ON DELETE CASCADE,
    remote_contact_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (binding_id, remote_contact_id)
);

CREATE TABLE conversation_mappings (
    binding_id TEXT NOT NULL REFERENCES backend_bindings(binding_id) ON DELETE CASCADE,
    remote_conversation_id TEXT NOT NULL,
    xmpp_address TEXT NOT NULL,
    conversation_kind TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (binding_id, remote_conversation_id),
    UNIQUE (binding_id, xmpp_address)
);

CREATE TABLE message_mappings (
    binding_id TEXT NOT NULL REFERENCES backend_bindings(binding_id) ON DELETE CASCADE,
    client_message_id TEXT NOT NULL,
    remote_message_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (binding_id, client_message_id),
    UNIQUE (binding_id, remote_message_id)
);

CREATE TABLE media_references (
    binding_id TEXT NOT NULL REFERENCES backend_bindings(binding_id) ON DELETE CASCADE,
    media_id TEXT NOT NULL,
    opaque_reference TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (binding_id, media_id)
);
