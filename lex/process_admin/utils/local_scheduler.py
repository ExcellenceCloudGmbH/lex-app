import sched
import threading
import time
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

class LocalSchedulerBackend:
    """
    A pure-Python in-process scheduler backend used when CELERY_ACTIVE is false.
    Run loop is managed by a daemon thread using standard library `sched`.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._event = threading.Event()
        # Custom delay function: wait on event to allow interruption
        self._scheduler = sched.scheduler(time.time, self._delay)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="LocalSchedulerWorker")
        self._running = True
        self._thread.start()

    def _delay(self, timeout):
        """Wait for `timeout` seconds, or until interrupt event is set."""
        if timeout > 0:
            self._event.wait(timeout)
        # Note: We don't clear here immediately because sched might call time() right after
        # But for our run loop, we clear in the worker loop.
        
    def _worker(self):
        logger.info("LocalSchedulerBackend thread started.")
        while self._running:
            # run() blocks until queue is empty or delay returns
            self._scheduler.run(blocking=True)
            # If here, queue is empty. Wait indefinitely for new tasks.
            self._event.wait()
            self._event.clear()

    def schedule(self, run_at_time, func, args=(), kwargs=None):
        """
        Schedule a function execution at a specific datetime.
        """
        if kwargs is None:
            kwargs = {}
        
        now = timezone.now()
        delay = (run_at_time - now).total_seconds()
        
        if delay < 0:
            delay = 0

        logger.info(f"LocalScheduler: Scheduling {func.__name__} in {delay:.2f}s")
        
        # Enter the event into the scheduler
        # Priority 1 (highest in sched is 0? No, lower numbers are higher priority)
        self._scheduler.enter(delay, 1, func, argument=args, kwargs=kwargs)
        
        # Wake up the worker thread so it can re-evaluate the next run time
        # (Crucial if the new task is earlier than the currently sleeping task)
        self._event.set()
