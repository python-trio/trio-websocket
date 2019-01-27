'''
This is a test for issue #96:
https://github.com/HyperionGray/trio-websocket/issues/96

This is not a part of the automated tests, because its prone to a race condition
and I have not been able to trigger it deterministically. Instead, this "test"
connects multiple times, triggering the bug on average once or twice out of
every 10 connections. The bug causes an uncaught exception which causes the
script to crash.

Note that the server handler and the client have `try/except ConnectionClosed`
blocks. The bug in issue #96 is that the exception is raised in a background
task, so it is not caught by the caller's try/except block. Instead, it crashes
the nursery that the background task is running in.

Here's an example run where the test fails:

(venv) $ python tests/issue_96.py
INFO:root:Connection 0
INFO:root:Connection 1
INFO:root:Connection 2
INFO:root:Connection 3
INFO:root:Connection 4
INFO:root:Connection 5
INFO:root:Connection 6
INFO:root:Connection 7
INFO:root:Connection 8
INFO:root:Connection 9
INFO:root:Connection 10
INFO:root:Connection 11
Traceback (most recent call last):
  File "tests/issue_96.py", line 58, in <module>
    trio.run(main)
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_core/_run.py", line 1337, in run
    raise runner.main_task_outcome.error
  File "tests/issue_96.py", line 54, in main
    nursery.cancel_scope.cancel()
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_core/_run.py", line 397, in __aexit__
    raise combined_error_from_nursery
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 327, in serve_websocket
    await server.run(task_status=task_status)
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 1097, in run
    await trio.sleep_forever()
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_core/_run.py", line 397, in __aexit__
    raise combined_error_from_nursery
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_highlevel_serve_listeners.py", line 129, in serve_listeners
    task_status.started(listeners)
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_core/_run.py", line 397, in __aexit__
    raise combined_error_from_nursery
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_highlevel_serve_listeners.py", line 27, in _run_handler
    await handler(stream)
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 1125, in _handle_connection
    await connection.aclose()
  File "/home/mhaase/code/trio-websocket/venv/lib/python3.6/site-packages/trio/_core/_run.py", line 397, in __aexit__
    raise combined_error_from_nursery
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 927, in _reader_task
    await handler(event)
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 811, in _handle_connection_closed_event
    await self._write_pending()
  File "/home/mhaase/code/trio-websocket/trio_websocket/_impl.py", line 968, in _write_pending
    raise ConnectionClosed(self._close_reason) from None
trio_websocket._impl.ConnectionClosed: <CloseReason code=1006 name=ABNORMAL_CLOSURE reason=None>

You may need to modify the sleep duration to reliably trigger the bug on your
system. On a successful test, it will make 1,000 connections without any
uncaught exceptions.
'''
import functools
import logging
from random import random

import trio
from trio_websocket import connect_websocket, ConnectionClosed, serve_websocket


logging.basicConfig(level=logging.INFO)


async def handler(request):
    ''' Reverse incoming websocket messages and send them back. '''
    try:
        ws = await request.accept()
        await ws.send_message('issue96')
        await ws._for_testing_peer_closed_connection.wait()
        await trio.aclose_forcefully(ws._stream)
    except ConnectionClosed:
        pass


async def main():
    async with trio.open_nursery() as nursery:
        serve = functools.partial(serve_websocket, handler, 'localhost', 8000,
            ssl_context=None)
        await nursery.start(serve)

        for n in range(1):
            logging.info('Connection %d', n)
            try:
                connection = await connect_websocket(nursery, 'localhost', 8000,
                    '/', use_ssl=False)
                await connection.get_message()
                await connection.aclose()
                await trio.sleep(.1)
            except ConnectionClosed:
                pass

        nursery.cancel_scope.cancel()


if __name__ == '__main__':
    trio.run(main)
