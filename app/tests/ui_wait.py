"""Drive the real Tk event loop until the latest asynchronous view is rendered."""
import time

def settle_view(app, timeout=5):
    app.refresh_table()
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        app.root.update()
        if (not app.search_timer and not app.query.busy and app.query.latest==app.view_request()
                and app.table_snapshot is not None and app.query_failed_request is None):
            return
        time.sleep(0.005)
    raise AssertionError('Latest task view did not finish: '+app.query_status.get())
