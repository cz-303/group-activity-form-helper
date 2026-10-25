"""Tray callbacks only enqueue events; Tk is exclusively updated by its UI thread."""

import threading


class Tray:
    def __init__(self, events):
        self.events = events
        self.icon = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            import pystray
            from brand_icon import make_icon

            picture = make_icon(64)

            def send(kind):
                return lambda icon, item: self.events.put((kind,))

            self.icon = pystray.Icon(
                'GroupFormHelper',
                picture,
                '群活动填表助手',
                pystray.Menu(
                    pystray.MenuItem('打开助手', send('tray_show'), default=True),
                    pystray.MenuItem('暂停任务处理', send('tray_pause')),
                    pystray.MenuItem('继续任务处理', send('tray_resume')),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem('退出选项', send('tray_exit')),
                ),
            )

            def setup(icon):
                if self.stopped.is_set():
                    icon.stop()
                    return
                icon.visible = True
                self.ready.set()
                self.events.put(('tray_ready',))

            self.icon.run(setup=setup)
        except Exception as e:
            self.events.put(('tray_failed', str(e)))
        finally:
            self.ready.clear()
            if not self.stopped.is_set():
                self.events.put(('tray_failed', '托盘已停止，助手窗口已恢复。'))

    def stop(self):
        self.stopped.set()
        if self.icon:
            self.icon.stop()
