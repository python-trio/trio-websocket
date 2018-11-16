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
timeouts could be a dangerous flaw. Therefore, this library takes a balanced
approach to timeouts, where high-level APIs have internal timeouts, but you may
disable them or use lower-level APIs if you want more control over the behavior.

Message Timeouts
----------------

As a motivating example, let's write a client that sends one message and then
expects to receive one message. To guard against a misbehaving server or
network, we want to place a 15 second timeout on this combined send/receive
operation. In other libraries, you might find that the APIs have ``timeout``
arguments, but that style of timeout is very tedious when composing multiple
operations. In Trio, we have helpful abstractions like cancel scopes, allowing
us to implement our example like this:

.. code-block:: python

    async with open_websocket_url('ws://my.example/') as ws:
        with trio.fail_after(15):
            await ws.send_message('test')
            msg = await ws.get_message()
            print('Received message: {}'.format(msg))

The 15 second timeout covers the cumulative time to send one message and to wait
for one response. It raises ``TooSlowError`` if the runtime exceeds 15 seconds.

Connection Timeouts
-------------------

The example in the previous section ignores one obvious problem: what if
connecting to the server or closing the connection takes a long time? How do we
apply a timeout to those operations? One option is to put the entire connection
inside a cancel scope:

.. code-block:: python

    with trio.fail_after(15):
        async with open_websocket_url('ws://my.example/') as ws:
            await ws.send_message('test')
            msg = await ws.get_message()
            print('Received message: {}'.format(msg))

The approach suffices if we want to compose all four operations into one
timeout: connect, send message, get message, and disconnect. But this approach
will not work if want to separate the timeouts for connecting/disconnecting from
the timeouts for sending and receiving. Let's write a new client that sends
messages periodically, waiting up to 15 seconds for a response to each message
before sending the next message.

.. code-block:: python

    async with open_websocket_url('ws://my.example/') as ws:
        for _ in range(10):
            await trio.sleep(30)
            with trio.fail_after(15):
                await ws.send_message('test')
                msg = await ws.get_message()
                print('Received message: {}'.format(msg))

In this scenario, the ``for`` loop will take at least 300 seconds to run, so we
would like to specify timeouts that apply to connecting and disconnecting but do
not apply to the contents of the context manager block. This is tricky because
the connecting and disconnecting are handled automatically inside the context
manager :func:`open_websocket_url`. Here's one possible approach:

.. code-block:: python

    with trio.fail_after(10) as cancel_scope:
        async with open_websocket_url('ws://my.example'):
            cancel_scope.deadline = math.inf
            for _ in range(10):
                await trio.sleep(30)
                with trio.fail_after(15):
                    await ws.send_message('test')
                    msg = await ws.get_message()
                    print('Received message: {}'.format(msg))
            cancel_scope.deadline = trio.current_time() + 5

This example places a 10 second timeout on connecting and a separate 5 second
timeout on disconnecting. This is accomplished by wrapping the entire operation
in a cancel scope and then modifying the cancel scope's deadline when entering
and exiting the context manager block.

This approach works but it is a bit complicated, and we don't want our safety
mechanisms to be complicated! Therefore, the high-level client APIs
:func:`open_websocket` and :func:`open_websocket_url` contain internal timeouts
that apply only to connecting and disconnecting. Let's rewrite the previous
example to use the library's internal timeouts:

.. code-block:: python

    async with open_websocket_url('ws://my.example/', connect_timeout=10,
            disconnect_timeout=5) as ws:
        for _ in range(10):
            await trio.sleep(30)
            with trio.fail_after(15):
                await ws.send_message('test')
                msg = await ws.get_message()
                print('Received message: {}'.format(msg))

Just like the previous example, this puts a 10 second timeout on connecting, a
separate 5 second timeout on disconnecting. These internal timeouts violate the
Trio philosophy of composable timeouts, but hopefully the examples in this
section have convinced you that breaking the rules a bit is justified by the
improved safety and ergonomics of this version.

In fact, these timeouts have actually been present in all of our examples so
far! We just didn't see them because those arguments have default values. If you
really don't like the internal timeouts, you can disable them by passing
``math.inf``, or you can use the low-level APIs instead.

Timeouts on Low-level APIs
--------------------------

In the previous section, we saw how the library's high-level APIs have internal
timeouts. The low-level APIs, like :func:`connect_websocket` and
:func:`connect_websocket_url` do not have internal timeouts, nor are they
context managers. These characteristics make the low-level APIs suitable for
situations where you want very fine-grained control over timeout behavior.

.. code-block:: python

    async with trio.open_nursery():
        with trio.fail_after(10):
            connection = await connect_websocket_url(nursery, 'ws://my.example/')
        try:
            for _ in range(10):
                await trio.sleep(30)
                with trio.fail_after(15):
                    await ws.send_message('test')
                    msg = await ws.get_message()
                    print('Received message: {}'.format(msg))
        finally:
            with trio.fail_after(5):
                await connection.aclose()

This example applies the same 10 second timeout for connecting and 5 second
timeout for disconnecting as seen in the previous section, but it uses the
lower-level APIs. This approach gives you more control but the low-level APIs
also require more boilerplate, such as creating a nursery and using try/finally
to ensure that the connection is always closed.

Server Timeouts
---------------

The server API also has internal timeouts. These timeouts are configured when
the server is created, and they are enforced on each connection.

.. code-block:: python

    async def handler(request):
        ws = await request.accept()
        msg = await ws.get_message()
        print('Received message: {}'.format(msg))

    await serve_websocket(handler, 'localhost', 8080, ssl_context=None,
        connect_timeout=10, disconnect_timeout=5)

The server timeouts work slightly differently from the client timeouts. The
server's connect timeout measures the time between receiving a new TCP
connection and calling the user's handler. The connect timeout
includes waiting for the client's side of the handshake (which is represented by
the ``request`` object), *but it does not include the server's side of the
handshake.* The server handshake needs to be performed inside the user's
handler, e.g. ``await request.accept()``. The disconnect timeout applies to the
time between the handler exiting and the connection being closed.

Each handler is spawned inside of a nursery, so there is no way for connect and
disconnect timeouts to raise exceptions to your code. (If they did raise
exceptions, they would cancel your nursery and crash your server!) Instead,
connect timeouts cause the connection to be silently closed, and the handler is
never called. For disconnect timeouts, your handler has already exited, so a
timeout will cause the connection to be silently closed.

As with the client APIs, you can disable the internal timeouts by passing
``math.inf`` or you can use low-level APIs like :func:`wrap_server_stream`.
