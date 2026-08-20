"""Task queue management for async research scraping jobs."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel


class TaskQueueJob(BaseModel):
    """Represent a queued job awaiting execution."""

    id: str
    task_type: str  # 'scrape', 'export', etc.
    payload: dict[str, Any]
    created_at: str = ""
    priority: int = 5  # 1=highest, 10=lowest


class ResearchTaskQueue:
    """In-memory task queue for coordinating background scraping jobs.
    
    In production, this would be replaced with Redis/RabbitMQ.
    For now, uses asyncio queues and task groups.
    """

    def __init__(self, max_workers: int = 3):
        """Initialize queue.
        
        Args:
            max_workers: Max concurrent workers (default 3)
        """
        self.max_workers = max_workers
        self.jobs: dict[str, TaskQueueJob] = {}
        self.running_tasks: set[asyncio.Task] = set()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: list[asyncio.Task] = []
        self._shutdown = False

    async def start(self) -> None:
        """Start worker tasks."""
        self._shutdown = False
        
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(i))
            self.workers.append(worker)

    async def stop(self) -> None:
        """Shutdown all workers gracefully."""
        self._shutdown = True
        
        # Wait for remaining jobs
        while not self.queue.empty():
            await asyncio.sleep(0.1)
        
        # Cancel workers
        for worker in self.workers:
            worker.cancel()
        
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop processing jobs from queue."""
        while not self._shutdown:
            try:
                # Get job with timeout
                job = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )
                
                # Execute job
                self.running_tasks.add(asyncio.create_task(self._execute_job(job, worker_id)))
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Worker {worker_id} error: {e}")

    async def _execute_job(self, job: TaskQueueJob, worker_id: int) -> None:
        """Execute a single job."""
        try:
            if job.task_type == "scrape":
                from workbench.collab.research_scraper import scrape_source
                
                result = await scrape_source(
                    rule=job.payload["source_config"],
                    url=job.payload["target_url"],
                )
                
                # TODO: Process result - save to database, trigger export, etc.
                
            elif job.task_type == "export":
                from workbench.collab.research_exporter import batch_export_task_results
                
                result = batch_export_task_results(
                    task=job.payload["task"],
                    db_path=Path(__file__).parent / "data" / "research_cache.db",
                )
                
            else:
                raise ValueError(f"Unknown job type: {job.task_type}")
            
        except Exception as e:
            print(f"Job {job.id} failed: {e}")
        finally:
            # Mark job as done
            self.jobs[job.id].status = "completed"
            self.running_tasks.discard(current_task)

    async def enqueue(self, job: TaskQueueJob) -> None:
        """Add job to queue."""
        job.created_at = datetime.utcnow().isoformat() + "Z"
        self.jobs[job.id] = job
        await self.queue.put(job)

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue statistics."""
        return {
            "queue_size": self.queue.qsize(),
            "running_jobs": len(self.running_tasks),
            "total_jobs": len(self.jobs),
            "workers_active": len([w for w in self.workers if not w.done()]),
        }


# Singleton instance for app-wide access
_task_queue: ResearchTaskQueue | None = None


def get_task_queue() -> ResearchTaskQueue:
    """Get or create global task queue instance."""
    global _task_queue
    if _task_queue is None:
        _task_queue = ResearchTaskQueue(max_workers=3)
    return _task_queue


async def init_research_queue() -> None:
    """Initialize and start the research task queue (called on app startup)."""
    global _task_queue
    _task_queue = ResearchTaskQueue(max_workers=3)
    await _task_queue.start()


async def shutdown_research_queue() -> None:
    """Gracefully shutdown the research task queue (called on app shutdown)."""
    global _task_queue
    if _task_queue:
        await _task_queue.stop()
