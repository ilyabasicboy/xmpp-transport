# Xabber XMPP Transport

A provider-neutral framework for connecting personal messengers to XMPP.
Telegram and MAX are the first planned backend adapters.

The project currently contains the foundation layer: provider-neutral domain
objects, commands, events, focused ports, a backend registry, configuration,
and lifecycle supervision.

## Development

```bash
python3 -m unittest discover -s tests
python3 -m xmpp_transport.runtime.app --help
```

Backend adapters are discovered from the `xabber_transport.backends` Python
entry-point group. Validate an installed backend without opening connections:

```bash
xabber-transport --config transports.ini --backend telegram --check-config
```

Runtime integrations and their external dependencies will be introduced in
later phases rather than leaking them into the domain package.
