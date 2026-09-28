CREATE UNIQUE INDEX backend_bindings_xmpp_account_backend_uq
    ON backend_bindings (xmpp_account_id, backend_id);

