.. _websocket-servers:

Servers
=======

.. currentmodule:: trio_websocket

Creating A Server
-----------------

This page goes into the details of creating a WebSocket server. Let's start by
revisiting the example from :ref:`server-tutorial`.

.. code-block:: python
    :linenos:

    import trio
    from trio_websocket import serve_websocket, ConnectionClosed

    async def echo_server(request):
        ws = await request.accept()
        while True:
            try:
                message = await ws.get_message()
                await ws.send_message(message)
            except ConnectionClosed:
                break

    async def main():
        await serve_websocket(echo_server, '127.0.0.1', 8000, ssl_context=None)

    trio.run(main)

.. note::

    A more complete example is included `in the repository
    <https://github.com/HyperionGray/trio-websocket/blob/master/examples/server.py>`__.

As explained in the tutorial, a WebSocket server needs a handler function and a
host and port to bind to. The handler function receives a
:class:`WebSocketRequest` object, and it calls ``accept()`` to finish the
handshake and obtain a
:class:`WebSocketConnection` object.

.. autofunction:: serve_websocket

Serving Arbitrary Stream
------------------------

The WebSocket protocol is defined as an application layer protocol that runs on
top of TCP, and the convenience functions described above automatically create
those TCP connections. In more obscure use cases, you might want to run the
WebSocket protocol on top of some other type of transport protocol. The library
includes a convenience function that allows you to wrap any arbitrary Trio
stream with a server WebSocket.

.. autofunction:: wrap_server_stream
