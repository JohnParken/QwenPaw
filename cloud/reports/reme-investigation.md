# ReMe lifecycle investigation

Status: **unsupported for the P0 snapshot contract**.

The P0 Runner therefore uses `NullMemory` and emits `memory: null`. This is
an explicit capability boundary; it does not claim that ReMe state is part of
the portable export.

Evidence from `src/qwenpaw/agents/memory/reme_light_memory_manager.py`:

- `ReMeLightMemoryManager` constructs an embedded ReMe application from the
  live agent configuration and working directory.
- Its lifecycle surface is `start()` and `close()`, plus ReMe job leases and
  background auto-memory workers. It does not provide the six P0 operations:
  `probe`, `quiesce`, `export_snapshot`, `validate_snapshot`,
  `restore_snapshot`, and deadline-aware `close`.
- `close()` returns a boolean after an internal fixed timeout; it does not
  accept the Runner deadline or return a portable snapshot token.
- ReMe jobs and embedding/index state are external mutable state, so copying
  the workspace files alone cannot prove a consistent memory snapshot.

To support ReMe in a later protocol revision, add an adapter that fences all
active jobs, records backend/version and scope identity, exports a versioned
snapshot, validates it before materialization, and propagates Runner
deadlines through close. Until those guarantees exist, selecting ReMe for a
P0 export would make restore semantics unverifiable.
