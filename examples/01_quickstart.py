# examples/01_quickstart.py
"""
Quickstart: Queue a planning job, run the worker, and read the result.

This covers the most common workflow — 90% of what you'll need.

Prerequisites:
    - Ollama, LM Studio, or llama.cpp running with a loaded model
    - pip install fitz-forge
"""

# ============================================================
# Option A: CLI (recommended for most users)
# ============================================================
#
# Open a terminal and run:
#
#   fitz plan "Add OAuth2 authentication with Google and GitHub providers"
#   fitz run            # starts the background worker
#   fitz status 1       # check progress
#   fitz get 1          # print the finished plan
#
# That's it. The worker processes jobs sequentially from the SQLite queue.
# It runs until you press Ctrl+C.

# ============================================================
# Option B: Python SDK (for programmatic use)
# ============================================================

import asyncio

from fitz_forge.config import load_config
from fitz_forge.models import SQLiteJobStore
from fitz_forge.tools.create_plan import create_plan
from fitz_forge.tools.check_status import check_status
from fitz_forge.tools.get_plan import get_plan


async def main():
    config = load_config()
    store = SQLiteJobStore(config.db_path)
    await store.initialize()

    # Queue a job
    result = await create_plan(
        description="Build a plugin system for data transformations",
        timeline=None,
        context=None,
        integration_points=None,
        api_review=False,
        store=store,
        config=config,
    )
    job_id = result["job_id"]
    print(f"Queued job: {job_id}")

    # Note: You still need to run `fitz run` in another terminal
    # to process the job. The SDK queues work, the worker executes it.

    # Check status
    status = await check_status(job_id, store)
    print(f"Status: {status['state']}, Progress: {status['progress']}")

    # Once complete, retrieve the plan
    # plan = await get_plan(job_id, store, config)
    # print(plan["markdown"])


if __name__ == "__main__":
    asyncio.run(main())
