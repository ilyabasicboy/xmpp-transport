CREATE TABLE incoming_message_deliveries (
    binding_id TEXT NOT NULL REFERENCES backend_bindings(binding_id) ON DELETE CASCADE,
    remote_message_id TEXT NOT NULL,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (binding_id, remote_message_id)
);

