# Task Notes: Unified XMPP Transport Framework

## Goal

Build a new provider-neutral XMPP transport framework from scratch in:

```text
/home/ilya.basyrov/Projects/xmpp-transport
```

The framework must support multiple personal-messenger backends, starting with
Telegram and MAX. It must provide one shared XMPP transport implementation and
one shared Xabber Server helper module while keeping messenger SDKs, protocols,
authorization flows, and provider-specific state isolated in backend adapters.

The existing Telegram and MAX transports are source references. Reuse proven
code and behavior deliberately, but do not merge their current large transport
classes or preserve provider-specific coupling in the new core.

## Project Map

### New source projects

- Unified transport framework:
  `/home/ilya.basyrov/Projects/xmpp-transport`
- Unified server module (project directory created; implementation starts in Phase 6):
  `/home/ilya.basyrov/Projects/module-transport`

### Transport references

- Telegram transport alpha:
  `/home/ilya.basyrov/Projects/xmpp-transport-telegram`
- MAX transport:
  `/home/ilya.basyrov/Projects/xmpp-transport-max`
- Telegram server module:
  `/home/ilya.basyrov/Projects/module-transport-telegram`
- MAX server module:
  `/home/ilya.basyrov/Projects/module-transport-max`

### Xabber references

- Main panel project:
  `/home/ilya.basyrov/Projects/xabber-server-panel`
- Legacy Xabber Web:
  `/home/ilya.basyrov/Projects/xabber-server-panel/modules/xabber_web`
- Xabber Web NG:
  `/home/ilya.basyrov/Projects/xabber-web-ng`
- XMPP server sources:
  `/home/ilya.basyrov/Projects/xabber-xmpp-server`
- Xabber protocol knowledge and custom XEP drafts:
  `/home/ilya.basyrov/Projects/xabber-knowledge`
- Compiled local XMPP server:
  `/home/ilya.basyrov/Projects/xabber-server-panel/xmpp`

## Development Rules

- Treat all projects listed above as references. Inspect them only when the
  current task needs that context.
- Do not invent, infer, simplify, or redesign backend behavior while porting it.
  Before implementing a provider flow, inspect the corresponding old transport,
  its tests, and its observable user interaction. Preserve that behavior unless
  the task explicitly requests a change.
- Internal code may be optimized, refactored, or simplified when observable
  behavior remains equivalent: user actions, state transitions, protocol
  payloads, ordering, error handling, and recovery semantics must not change.
  Cover behavioral equivalence with tests before relying on the optimization.
- In particular, MAX QR authorization continues automatically after `/login`,
  exactly as in `xmpp-transport-max`; do not introduce a `/continue` command or
  any other user action that the original flow does not require.
- Put all normal framework source changes in `xmpp-transport`.
- Put all normal unified helper-module changes in `module-transport` once that
  project exists.
- Do not modify the old Telegram/MAX transports while extracting code unless a
  task explicitly asks for a compatibility fix there.
- Do not modify `xabber-server-panel`, either Xabber Web project,
  `xabber-xmpp-server`, or the compiled `xmpp` tree as source work.
- Temporary diagnostic instrumentation and deployment of a rebuilt local module
  into the compiled server are allowed for live testing. Remove temporary
  diagnostics after the investigation unless explicitly asked to keep them.
- Read the relevant custom XEP material from `xabber-knowledge` before designing
  or changing Xabber-specific protocol payloads.
- Keep the project compatible with Python 3.9. Do not use Python 3.10-only type
  syntax such as `A | B`; use `Optional[A]` and `Union[A, B]`.
- Prefer readable code with explicit dependencies. Add comments where protocol
  behavior, security boundaries, ordering, idempotency, or recovery rules would
  otherwise be difficult to recover from the code.
- Never log or expose authorization codes, passwords, session strings, access
  tokens, full private message bodies, private media URLs, or raw protocol dumps.
- Use narrow, filtered diagnostics and structured metadata such as backend ID,
  binding ID, event ID, and correlation ID.
- PostgreSQL is the source of truth for persistent framework state. The
  transport must never write directly to Xabber Server database tables.

## Technology Baseline

- Python 3.9
- `asyncio` for concurrency, task supervision, queues, and lifecycle
- `aiohttp` for HTTP/WebSocket clients and the small HTTP server
- `slixmpp` for XEP-0114 external component connections
- `asyncpg` for PostgreSQL
- `cryptography` for encrypted backend credentials
- `Telethon` inside the Telegram adapter
- MAX HTTP/WebSocket implementation inside the MAX adapter
- `pillow` only when image processing is actually required
- `aio-pika` only if RabbitMQ is introduced later

Do not make `aiohttp`, `slixmpp`, `asyncpg`, Telethon, or MAX wire types visible
inside the domain and application layers.

## Architectural Direction

Use a lightweight hexagonal architecture with SOLID boundaries:

```text
Telegram adapter --\
                    >-- backend ports --> application/domain <-- XMPP adapter
MAX adapter --------/                          ^
                                               |
                                      PostgreSQL/HTTP adapters
```

Dependency direction:

- `domain` depends only on the Python standard library.
- `application` depends on `domain` and port protocols.
- `adapters` depend on application/domain and external libraries.
- `runtime` is the composition root and is the only place that assembles
  concrete implementations.
- Core code must not import from a Telegram or MAX package.

Prefer composition over inheritance. Do not build a large `BaseBackend` or a
large universal backend interface with unsupported methods. Use small feature
ports and backend capabilities.

## Proposed Package Layout

```text
xmpp_transport/
  domain/
    identifiers.py
    models.py
    events.py
    commands.py
    errors.py

  application/
    auth_service.py
    session_supervisor.py
    message_router.py
    roster_sync.py
    group_sync.py
    media_service.py
    command_service.py
    loop_guard.py

  ports/
    backend.py
    events.py
    repositories.py
    xmpp.py
    media.py

  adapters/
    backends/
      telegram/
      max/
    xmpp/
    postgres/
    web/

  runtime/
    config.py
    registry.py
    lifecycle.py
    app.py
```

The precise file split may change as contracts become clear. Preserve the layer
boundaries even if fewer files are initially sufficient.

## Core Domain Model

Use provider-neutral types for data crossing the backend boundary:

- `BackendId`
- `BindingId`
- `RemoteAccount`
- `RemoteObjectId`
- `Contact`
- `Conversation`
- `Participant`
- `IncomingMessage`
- `OutgoingMessage`
- `Media`
- `Avatar`
- `ReplyReference`
- `ForwardReference`

Remote IDs must be opaque strings in the shared model. Do not assume that all
providers use numeric identifiers.

Provider SDK objects and raw payloads must remain inside their adapter. Map them
to domain types before passing them to application services.

## Backend Contracts

Use two primary abstraction levels:

- `BackendPlugin`: provider descriptor and factory for authentication and
  per-binding sessions.
- `BackendSession`: one live connection between an XMPP account binding and a
  remote messenger account.

Split optional behavior into narrow feature ports, for example:

- `MessageSender`
- `ContactSource`
- `ConversationSource`
- `MediaTransfer`
- `ButtonActions`
- `MessageMutations`
- `GroupAdministration`

The presence of a feature port is the source of truth. A capability descriptor
may expose those features for UI and diagnostics, but it must not contradict the
available ports.

Adding a backend should require implementing and registering a plugin, its
authentication/session adapters, mappings, configuration, migrations, and
contract tests. It should not require editing common XMPP routing, roster/group
sync, the HTTP runtime, or other backends.

## Event Model

Backends publish typed domain events through `BackendEventSink`, including:

- `MessageReceived`
- `MessageChanged`
- `ConversationChanged`
- `ContactChanged`
- `AuthorizationLost`
- `SessionStateChanged`

Start with an in-process implementation based on `asyncio.Queue`. Preserve event
ordering per binding by processing each binding through a sequential queue.

Define stable event envelopes early:

- `event_id`
- `event_type`
- `schema_version`
- `backend_id`
- `binding_id`
- `occurred_at`
- `correlation_id` where applicable

Do not put SDK objects into events. Handlers must be idempotent because events
may eventually be delivered more than once.

Keep the event interfaces independent of `asyncio.Queue`. RabbitMQ can later be
added with `aio-pika` if the XMPP gateway and backend workers need to run in
separate processes. PostgreSQL remains the source of truth. If durable event
publication is introduced, use a transactional outbox.

Do not send media bodies through a broker. Pass metadata and opaque references;
stream content over HTTP or through object storage.

## Runtime and Process Model

Support a common codebase without requiring all providers to share a process.
The target runtime should be able to run:

```text
xabber-transport --backend telegram
xabber-transport --backend max
xabber-transport --config transports.ini
```

Initially prefer one backend per production process/container for fault
isolation and independent rollout. A multi-backend process can be supported by
the same composition root.

Each configured backend instance should normally keep its own XMPP component
domain, for example:

```text
telegram.example.com
max.example.com
```

This preserves existing JIDs, avoids remote-ID collisions, makes the provider
selection explicit, and permits independent deployment.

The user-facing control contact is a valid JID under that component domain,
normally `bot@telegram.example.com` or `bot@max.example.com`. Provider contact
and conversation JIDs use separate localparts.

Every background task must have a clear owner and shutdown path. Python 3.9 has
no `asyncio.TaskGroup`, so implement explicit supervision with `create_task`,
`gather`, cancellation, and deterministic resource cleanup.

Set timeouts on external operations, bound concurrency for media/avatar/sync
work, and move blocking image or subprocess work off the event loop.

## Authentication

Model authentication as a provider-driven state machine with shared public
states such as:

```text
IDLE -> WAITING_QR -> WAITING_PASSWORD/WAITING_CONFIRMATION -> CONNECTED
                                                        \-> EXPIRED/FAILED
```

The backend owns provider-specific transitions and returns typed challenges.
The shared application layer owns presentation, expiry, notifications, and
secure credential persistence.

For compatibility with the existing transports, MAX two-factor passwords are
submitted through the XMPP control chat. The transport must never log command
bodies or authentication secrets.

## Persistence

The shared schema should be provider-aware and cover stable framework concepts:

- XMPP accounts
- backend bindings
- binding status and encrypted credentials
- roster sync records and signatures
- conversation mappings
- message mappings
- media references
- event outbox if durable messaging is introduced

Every shared row belonging to a provider connection must be scoped by a binding
or backend ID. Enforce that one remote personal account can have only one active
XMPP owner per backend where required by product semantics.

Do not force all provider state into generic JSON. Provider adapters may own
separate Telegram/MAX tables and migrations for state that has no stable shared
meaning.

## XMPP and Server Module Boundary

The shared XMPP layer owns:

- XEP-0114 component lifecycle
- stanza parsing and serialization
- provider-neutral message routing
- replies, forwards, origin IDs, deduplication, and loop suppression
- supported Xabber group/conversation protocol flows
- client-visible errors and delivery results

The planned unified Xabber Server module is a privileged roster helper only. It
must be provider-neutral and limited to:

- `add-roster-contact`
- `rename-roster-contact`
- `remove-roster-contact`

Configure it with an allowlist of component domains and provider presentation
data such as the roster circle name. It must verify that requests come from an
allowed component and that managed contact JIDs belong to the appropriate
component domain.

Do not add module operations for groups, members, avatars, archives, messages,
media, permissions, fanout, or loop suppression. First document an integration
gap if a feature cannot be expressed through supported XMPP, custom XEP flows,
hooks, or documented server APIs.

## SOLID and Code Quality Rules

- Single Responsibility: separate connection lifecycle, authorization, message
  routing, roster sync, group sync, media, persistence, and protocol mapping.
- Open/Closed: register a new backend without branching on provider names in
  application services.
- Liskov Substitution: a feature port must honor its contract; do not expose
  methods that commonly raise `NotImplementedError`.
- Interface Segregation: use focused backend feature ports instead of one large
  interface.
- Dependency Inversion: application services depend on protocols; concrete
  Telethon, MAX, XMPP, HTTP, and PostgreSQL implementations are wired only in
  the composition root.
- Use constructor injection. Avoid global mutable registries and service
  locators inside business code.
- Use strong identifier types instead of passing unrelated IDs as anonymous
  strings throughout the system.
- Keep orchestration services small enough that routing, mapping, synchronization,
  and lifecycle can be tested independently.

## Testing Strategy

Use three levels of automated tests:

1. Domain/application unit tests with in-memory repositories and fake ports.
2. A shared backend contract suite run against Telegram and MAX fixtures.
3. Integration tests for Telethon, MAX HTTP/WebSocket, PostgreSQL, XMPP, and the
   server helper module.

Backend contract tests should cover at least:

- idempotent start and close
- stable provider/binding identity on emitted events
- ordered per-binding event handling
- send result and error normalization
- authorization loss
- duplicate command/event handling
- reconnect and shutdown behavior

Add architecture tests that reject imports of `telethon`, `aiohttp`, `asyncpg`,
and `slixmpp` from the domain layer, and reject imports from provider adapters in
the application layer.

## Implementation Plan

### Phase 1: Foundation

- Create packaging, lint/test configuration, entry point, and basic runtime.
- Define strong IDs, canonical domain models, errors, commands, and events.
- Define small backend, event, repository, XMPP, media, and clock ports.
- Add in-memory fakes and architecture tests.

### Phase 2: Shared infrastructure

- Implement configuration and backend registry.
- Implement PostgreSQL migrations and shared repositories.
- Implement the in-process event bus and per-binding event processors.
- Implement lifecycle supervision and graceful shutdown.
- Implement liveness/readiness endpoints.

### Phase 3: XMPP application path

- Extract and normalize the proven XMPP XML helpers from the existing projects.
- Implement the shared XEP-0114 gateway.
- Implement direct-message routing, message mappings, deduplication, replies,
  forwards, and error delivery.
- Add contract/integration tests before adding provider details.

### Phase 4: Telegram backend

- Port Telethon client/session and QR authentication behind the new contracts.
- Port contacts, direct conversations, inbound/outbound text, and restart
  recovery.
- Add roster sync, avatars, groups, and media incrementally.
- Preserve the existing `telegram.example.com` JID behavior.

### Phase 5: MAX backend

- Adapt the existing MAX authentication, WebSocket lifecycle, snapshots, and
  deduplication without moving MAX wire behavior into the shared core.
- Port contacts, messages, groups, buttons, and media through feature ports.
- Preserve the existing `max.example.com` JID behavior.

### Phase 6: Unified server module

- Create the provider-neutral roster module from the two existing module
  implementations.
- Add component-domain allowlisting and generic roster operation payloads.
- Verify Telegram and MAX against the same module build.

Current progress: the provider-neutral `mod_transport` source and panel package
are implemented in `/home/ilya.basyrov/Projects/module-transport`. The shared
roster IQ namespace is `urn:xabber:transport:roster:1`; each allowlisted
component is restricted to roster contacts in its own component domain. Live
verification of both providers against one installed module build remains.

### Phase 7: Migration and hardening

- Compare observable behavior with both existing transports.
- Plan data migrations for sessions, bindings, roster signatures, message
  mappings, and media references.
- Add reconnect, duplicate delivery, failure recovery, security, and load tests.
- Roll out one provider at a time while retaining the old services for rollback.

## Reuse Policy

Before copying code from either old transport:

1. Read the original implementation and tests; do not reconstruct backend logic
   from memory or assumptions.
2. Identify its responsibility, external assumptions, state transitions, and
   observable user interaction.
3. Add or preserve tests that describe its observable behavior.
4. Remove provider-specific names only when the concept is genuinely shared.
5. Map provider objects at the adapter boundary rather than weakening the shared
   domain model.
6. Prefer extracting small proven algorithms and protocol helpers over copying
   entire `transport.py`, `component.py`, or repository classes.

The old transports are references during development, not runtime or package
dependencies of the new framework.

## Initial Definition of Success

The first complete vertical slice is:

1. Start one configured component.
2. Bind one Telegram account through secure authorization.
3. Synchronize direct contacts through the unified roster module.
4. Deliver inbound and outbound direct text messages.
5. Restart and restore the binding without duplicate roster items or messages.
6. Run the same application path with a MAX plugin without changing shared
   routing and synchronization services.

The practical architecture test is that a third backend can be added through a
new adapter, configuration, migrations, and contract tests without changing the
shared XMPP/application logic or either existing backend.
