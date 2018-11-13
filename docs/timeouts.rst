Timeouts
========

.. currentmodule:: trio_websocket

Networking code is inherently complex due to the unpredictable nature of network
failures and the possibility of a remote peer that is coded incorrectly—or even
maliciously! Therefore, your code needs to deal with unexpected circumstances.
One common failure mode that you should guard against is a slow or unresponsive
peer.

This page describes the timeout behavior in ``trio-websocket`` and shows various
examples for implementing timeouts in your own code. Before reading this, you
might find it helpful to read `"Timeouts and cancellation for humans"
<https://vorpus.org/blog/timeouts-and-cancellation-for-humans/>`__, an article
written by Trio's author that describes an overall philosophy regarding
timeouts. The short version is that Trio discourages libraries from using
internal timeouts. Instead, it encourages the caller to enforce timeouts, which
makes timeout code easier to compose and reason about.

On the other hand, this library is intended to be safe to use, and omitting
timeouts could be a dangerous flaw. Therefore, this library contains takes a
balanced approach to timeouts, where high-level APIs have internal timeouts, but
you may disable them or use lower-level APIs if you want more control over the
behavior.

Built-in Client Timeouts
------------------------

The high-level client APIs :func:`open_websocket` and :func:`open_websocket_url`
contain built-in timeouts for connecting to a WebSocket and disconnecting from a
WebSocket. These timeouts are built-in for two reasons:

1. Omitting timeouts may be dangerous, and this library strives to make safe
   code easy to write.
2. These high-level APIs are context managers, and composing timeouts with
   context managers is tricky.

These built-in timeouts make it easy to write a WebSocket client that won't hang
indefinitely if the remote endpoint or network are misbehaving. The following
example shows a connect timeout of 10 seconds. This guarantees that the block
will start executing (reaching the line that prints "Connected") within 10
seconds. When the context manager exits after the ``print(Received response:
…)``, the disconnect timeout guarantees that it will take no more than 5 seconds
to reach the line that prints "Disconnected". If either timeout is exceeded,
then the entire block raises ``trio.TooSlowError``.

.. code-block:: python

    async with open_websocket_url('ws://my.example/', connect_timeout=10,
            disconnect_timeout=5) as ws:
        print("Connected")
        await ws.send_message('hello from client!')
        response = await ws.get_message()
        print('Received response: {}'.format(response))
    print("Disconnected")

.. note::

    The built-in timeouts do not affect the contents of the block! In this
    example, the client waits to receive a message. If the server never sends a
    message, then the client will block indefinitely on ``ws.get_message()``.
    Placing timeouts inside blocks is discussed below.

What if you decided that you really wanted to manage the timeouts yourself? The
following example implements the same timeout behavior explicitly, without
relying on the library's built-in timeouts.

.. code-block:: python

    with trio.move_on_after(10) as cancel_scope:
        async with open_websocket_url('ws://my.example',
                connect_timeout=math.inf, disconnect_timeout=math.inf):
            print("Connected")
            cancel_scope.deadline = math.inf
            await ws.send_message('hello from client!')
            response = await ws.get_message()
            print('Received response: {}'.format(response))
            cancel_scope.deadline = trio.current_time() + 5
        print("Disconnected")

Notice that the library's internal timeouts are disabled by passing
``math.inf``. This example is less ergonomic than using the built-in timeouts.
If you really want to customize this behavior, you may want to use the low-level
APIs instead, which are discussed below.

Timeouts Inside Blocks
----------------------

The built-in timeouts do not apply to the contents of the block. One of the
examples above would hang on ``ws.get_message()`` if the remote endpoint never
sends a message. If you want to enforce a timeout in this situation, you must to
do it explicitly:

.. code-block:: python

    async with open_websocket_url('ws://my.example/', connect_timeout=10,
            disconnect_timeout=5) as ws:
        with trio.fail_after(15):
            msg = await ws.get_message()
            print('Received message: {}'.format(msg))

This example waits up to 15 seconds to get one message from the server, raising
``trio.TooSlowError`` if the timeout is exceeded. Notice in this example that
the message timeout is larger than the connect and disconnect timeouts,
illustrating that the connect and disconnect timeouts do not apply to the
contents of the block.

Alternatively, you might apply one timeout to the entire operation: connect to
the server, get one message, and disconnect.

.. code-block:: python

    with trio.fail_after(15):
        async with open_websocket_url('ws://my.example/',
                connect_timeout=math.inf, disconnect_timeout=math.inf) as ws:
            msg = await ws.get_message()
            print('Received message: {}'.format(msg))

Note that the internal timeouts are disabled in this example.

Timeouts on Low-level APIs
--------------------------

We saw an example above where explicit timeouts were applied to the context
managers. In practice, if you need to customize timeout behavior, the low-level
APIs like :func:`connect_websocket_url` etc. will be clearer and easier to use.
This example implements the same timeouts above using the low-level APIs.

.. code-block:: python

    with trio.fail_after(10):
        connection = await connect_websocket_url('ws://my.example/')
    print("Connected")
    try:
        await ws.send_message('hello from client!')
        response = await ws.get_message()
        print('Received response: {}'.format(response))
    finally:
        with trio.fail_after(5):
            await connection.aclose()
        print("Disconnected")

The low-level APIs make the timeout code easier to read, but we also have to add
try/finally blocks if we want the same behavior that the context manager
guarantees.

Built-in Server Timeouts
------------------------

The server API also offer built-in timeouts. These timeouts are configured when
the server is created, and they are enforced on each connection.

.. code-block:: python

    async def handler(request):
        ws = await request.accept()
        msg = await ws.get_message()
        print('Received message: {}'.format(msg))

    await serve_websocket(handler, 'localhost', 8080, ssl_context=None,
        connect_timeout=10, disconnect_timeout=5)

The server timeouts work slightly differently from the client timeouts. The
connect timeout measures the time between when a TCP connection is received and
when the user's handler is called. As a consequence, the connect timeout
includes waiting for the client's side of the handshake, which is represented by
the ``request`` object. *It does not include the server's side of the
handshake,* because the server handshake needs to be performed inside the user's
handler, i.e. ``await request.accept()``. The disconnect timeout applies to the
time between the handler exiting and the connection being closed.

Each handler is spawned inside of a nursery, so there is no way for connect and
disconnect timeouts to raise exceptions to your code. Instead, connect timeouts
result cause the connection to be silently closed, and handler is never called.
For disconnect timeouts, your handler has already exited, so a timeout will
cause the connection to be silently closed.

As with the client APIs, you can disable the internal timeouts by passing
``math.inf``.
