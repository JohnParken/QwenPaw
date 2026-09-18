# Storage boundary (implementation contract)

Repository retains contracts.py public methods (UUIDs as strings, created_at datetimes, JSON as dicts).
Add open/put_definition/definition; messages accepts after=0.
All services MUST NOT use repository.pool, connection, SQL, or private _fence.

Additional API:
- ready() -> None; statistics() -> {runs: {status: count}, tool_calls: {status: count}, oldest_queued_seconds: float, active_sandboxes: int, queue_seconds_mean_5m: float}.
- validate_execution(ctx, allow_cancelled=False) -> run dict. Check principal/session/worker/definition/epoch/lease/current active run. Reject cancellation unless allowed.
- sandbox_acquire(ctx), sandbox_release(session_id), sandbox_idle(idle_seconds) -> list[str].
- sandbox_stop_guard(session_id, expected=None) async context manager -> bool. Serializes session against claim/import/tools/other teardown; false for obsolete expected run/epoch or busy idle-stop. True holds exclusion through external deletion. Successful exit sets stopped; failure retains exclusion via still-active run or rollback. No service updates phase itself.
- session_import_guard(user_id,session_id,ctx=None) async context manager -> None. No ctx requires NO queued/active tasks. With ctx verify identity+lease+no cancellation and allow queued successors. Hold session exclusion during filesystem write.
- ensure_vector_support() -> None.
- pending_memory_vectors(user_id,model,limit=100) -> list[{id,text}].
- put_memory_vector(user_id,memory_id,model,vector:list[float]) -> None (do nothing if deleted, never resurrect).
- search_memory_vectors(user_id,model,vector,limit=10) -> list[{id,text}].
- knowledge_indexed(version,model) -> set[int]; put_knowledge_vector(version,document_id,text,model,vector); search_knowledge_vectors(version,model,vector,limit=10) -> list[str].
- claim_memory(owner:str,lease_seconds=300) -> job|None. Job includes run_id,user_id,owner,epoch,lease_until,attempts. At most one running per user. Epoch increments on every claim including same-owner retry. Max 3 attempts.
- merge_memory(job,facts:list[str]) -> bool: fenced job writes deduplicated facts associated with originating session; false if expired/cancelled/reclaimed.
- complete_memory(job) -> bool; fail_memory(job,error:str) -> None: both fenced by owner+epoch+lease. Complete only after successful vector indexing. Error queues retry or fails at third attempt.

Lease expiration does NOT free session. interrupt(run_id,epoch) requires expired lease and the caller's confirmed sandbox termination; preserves durable events, marks unfinished tools unknown and approvals expired.
Cancellation wins concurrent completion. Finish atomically saves state/messages/terminal event and enqueues memory extraction only for completed runs.
Memory test backend is SQLite :memory:, same repository business logic, serialized transaction writer; no claim that it reproduces InnoDB locking or survives restarts.
