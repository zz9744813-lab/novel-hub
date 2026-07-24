"""ARQ worker entry point - compatible with arq CLI and direct python run."""
import asyncio
import sys

async def _main():
    from arq import Worker
    from app.workers.arq_worker import WorkerSettings
    worker = Worker(WorkerSettings)
    await worker.run()

if __name__ == "__main__":
    asyncio.run(_main())
