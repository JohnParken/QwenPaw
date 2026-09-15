"""Test worker subprocess resumes a previously queried allocation over HTTP."""
import asyncio
import os
import json
from pathlib import Path
import sys
import time
from qwenpaw_cloud.__main__ import Config
from qwenpaw_cloud.sandbox import LeaseWatchdog
from qwenpaw_cloud.worker import Worker

config = Config.model_validate_json(Path(sys.argv[1]).read_text())
assignment = json.loads(Path(sys.argv[2]).read_text())
worker = Worker(
    worker_id=config.worker_id,
    core_url=config.core_url,
    file_url=config.file_url,
    token=Path(config.token_file).read_text(),
    root=Path(config.root),
    runtime_identity=config.runtime_identity,
    executor=config.executor,
    limits=config.limits,
)
# The test starts a new Supervisor for a confirmed current allocation, and
# obtains a fresh renewal before any Runner initialization/execution.
action = {
    key: assignment["context"][key] for key in ("attempt_id", "lease_epoch")
}
watch = LeaseWatchdog(config.limits)

sent = time.monotonic()
renewal = worker.core.post("/v1/heartbeat", action)
assert watch.renew(sent, renewal["lease_seconds"])

if os.getenv("P0_TEST_COMMIT_BARRIER"):
    original_post = worker.core.post

    def barrier_post(path, body=None):
        result = original_post(path, body)
        if path == "/v1/checkpoints":
            Path(os.environ["P0_TEST_COMMIT_BARRIER"]).write_text(
                json.dumps({"body": body, "result": result})
            )
            # Deterministic committed barrier. Parent kills this Supervisor.
            import threading

            threading.Event().wait(30)
            raise RuntimeError("kill barrier deadline exceeded")
        return result

    worker.core.post = barrier_post
print(json.dumps(asyncio.run(worker.execute_assignment(assignment, watch))))
