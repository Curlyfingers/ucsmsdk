from asyncio import sleep
from asyncio import CancelledError


class Timer(object):
    def __init__(self, interval, coroutine, loop=None, *args, **kwargs):
        self._loop = loop
        if not self._loop:
            from asyncio import get_event_loop
            self._loop = get_event_loop()

        self.interval = interval
        self.coroutine = coroutine
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}

    def cancel(self):
        self._task.cancel()

    async def _run(self, *args):
        while True:
            try:
                await self.coroutine(*args)
                await sleep(self.interval)
            except CancelledError:
                break

    def start(self, *args):
        self._task = self._loop.create_task(self.coroutine())
        self._loop.call_soon(self._task, *args)
