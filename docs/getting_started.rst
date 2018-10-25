Getting Started
===============

.. currentmodule:: trio_websocket

Installation
------------

This library supports Python ≥3.5. The easiest installation method is to use
PyPI.

::

    $ pip3 install trio-websocket

You can also install from source. Visit `the project's GitHub page <https://github.com/hyperiongray/trio-websocket/>`__, where you can clone the repository or download a Zip file.
Change into the project directory and run the following command.

::

    $ pip3 install .

If you want to contribute to development of the library, also see
:ref:`developer-installation`.

.. _client-tutorial:

Client Tutorial
---------------

This example briefly demonstrates how to create a WebSocket client.

.. code-block:: python
    :linenos:

    import trio
    from trio_websocket import open_websocket_url


    async def main():
        try:
            async with open_websocket_url('wss://localhost/foo') as ws:
                await ws.send_message('hello world!')
                message = await ws.get_message()
                logging.info('Received message: %s', message)
        except OSError as ose:
            logging.error('Connection attempt failed: %s', ose)

    trio.run(main)

The function :func:`open_websocket_url` is a context manager that automatically
connects and performs the WebSocket handshake before entering the block. This
ensures that the connection is usable before ``ws.send_message(…)`` is called.
The context manager yields a :class:`WebSocketConnection` instance that is used
to send and receive messages. The context manager also closes the connection
before exiting the block.

For more details and examples, see :ref:`websocket-clients`.

.. _server-tutorial:

Server Tutorial
---------------

This example briefly demonstrates how to create a WebSocket server. This server
is an *echo server*, i.e. it responds to each incoming message by sending back
an identical message.

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

The function :func:`serve_websocket` requires a function that can handle each
incoming connection. This handler function receives a
:class:`WebSocketRequest` object that the server can use to inspect the client's
handshake. Next, the server accepts the request in order to complete the
handshake and receive a :class:`WebSocketConnection` instance that can be used
to send and receive messages.

For more details and examples, see :ref:`websocket-servers`.
