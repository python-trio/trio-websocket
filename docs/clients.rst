.. _websocket-clients:

Clients
=======

.. currentmodule:: trio_websocket

Client Tutorial
---------------

This page goes into the details of creating a WebSocket client. Let's start by
revisiting the :ref:`client-example`.

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

.. note::

    A more complete example is included `in the repository
    <https://github.com/HyperionGray/trio-websocket/blob/master/examples/client.py>`__.

As explained in the tutorial, ``open_websocket_url(…)`` is a context manager
that ensures the connection is properly opened and ready before entering the
block. It also ensures that the connection is closed before exiting the block.
This library contains two such context managers for creating client connections:
one to connect by host and one to connect by URL.

.. autofunction:: open_websocket
    :async-with: ws

.. autofunction:: open_websocket_url
    :async-with: ws

Custom Nursery
--------------

The two context managers above create an internal nursery to run background
tasks. If you wish to specify your own nursery instead, you should use the
the following convenience functions instead.

.. autofunction:: connect_websocket
.. autofunction:: connect_websocket_url

Custom Stream
-------------

The WebSocket protocol is defined as an application layer protocol that runs on
top of TCP, and the convenience functions described above automatically create
those TCP connections. In more obscure use cases, you might want to run the
WebSocket protocol on top of some other type of transport protocol. The library
includes a convenience function that allows you to wrap any arbitrary Trio
stream with a client WebSocket.

.. autofunction:: wrap_client_stream
