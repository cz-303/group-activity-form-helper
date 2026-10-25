"""One local file operation at a time; workers never call Tk or UI callbacks."""

import queue
import threading


class BackgroundJobs:
    def __init__(self):
        self._owner = threading.get_ident()
        self._results = queue.Queue()
        self._active = None
        self.thread = None
        self.closed = False

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError('Job control must run on the UI thread')

    @property
    def busy(self):
        return self._active is not None

    def start(self, work, success, failure, cleanup=lambda value: None):
        self._check_owner()
        if self.closed or self.busy:
            return False
        self._active = (success, failure, cleanup)

        def run():
            try:
                self._results.put((work(), None))
            except Exception as error:
                self._results.put((None, error))

        self.thread = threading.Thread(target=run, name='activity-file-operation', daemon=False)
        try:
            self.thread.start()
        except Exception:
            self._active = None
            raise
        return True

    def poll(self):
        self._check_owner()
        if not self.busy:
            return
        try:
            result, error = self._results.get_nowait()
        except queue.Empty:
            return
        success, failure, cleanup = self._active
        self._active = None
        try:
            if not self.closed:
                if error is not None:
                    failure(error)
                else:
                    success(result)
        finally:
            if result is not None:
                cleanup(result)

    def close(self):
        self._check_owner()
        self.closed = True
        # The application waits for a writing worker before exiting.
        self.poll()
