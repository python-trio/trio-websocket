Recipes
=======

.. currentmodule:: trio_websocket

This page contains notes and sample code for common usage scenarios with this
library.

Heartbeat
---------

If you wish to keep a connection open for long periods of time but do not need
to send messages frequently, then a heartbeat holds the connection open and also
detects when the connection drops unexpectedly. The following recipe
demonstrates how to implement a connection heartbeat using WebSocket's ping/pong
feature.

.. code-block:: python
    :linenos:

    async def heartbeat(ws, timeout, interval):
        '''
        Send periodic pings on WebSocket ``ws``.

        Wait up to ``timeout`` seconds to send a ping and receive a pong. Raises
        ``TooSlowError`` if the timeout is exceeded. If a pong is received, then
        wait ``interval`` seconds before sending the next ping.

        This function runs until cancelled.

        :param ws: A WebSocket to send heartbeat pings on.
        :param float timeout: Timeout in seconds.
        :param float interval: Interval between receiving pong and sending next
            ping, in seconds.
        :raises: ``ConnectionClosed`` if ``ws`` is closed.
        :raises: ``TooSlowError`` if the timeout expires.
        :returns: This function runs until cancelled.
        '''
        while True:
            with trio.fail_after(timeout):
                await ws.ping()
            await trio.sleep(interval)

    async def main():
        async with open_websocket_url('ws://my.example/') as ws:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(heartbeat, ws, 5, 1)
                # Your application code goes here:
                pass

    trio.run(main)

Note that the :meth:`~WebSocketConnection.ping` method waits until it receives a
pong frame, so it ensures that the remote endpoint is still responsive. If the
connection is dropped unexpectedly or takes too long to respond, then
``heartbeat()`` will raise an exception that will cancel the nursery. You may
wish to implement additional logic to automatically reconnect.

A heartbeat feature can be enabled in the `example client
<https://github.com/python-trio/trio-websocket/blob/master/examples/client.py>`__.
with the ``--heartbeat`` flag.
