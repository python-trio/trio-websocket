API
===

.. currentmodule:: trio_websocket

In addition to the convenience functions documented in :ref:`websocket-clients`
and :ref:`websocket-servers`, the API has several important classes described
on this page.

Requests
--------

.. class:: WebSocketRequest

    A request object presents the client's handshake to a server handler. The
    server can inspect handshake properties like HTTP headers, subprotocols, etc.
    The server can also set some handshake properties like subprotocol. The
    server should call :meth:`accept` to complete the handshake and obtain a
    connection object.

    .. autoattribute:: headers
    .. autoattribute:: proposed_subprotocols
    .. autoattribute:: subprotocol
    .. autoattribute:: url
    .. automethod:: accept

Connections
-----------

.. class:: WebSocketConnection

    A connection object has functionality for sending and receiving messages,
    pinging the remote endpoint, and closing the WebSocket.

    .. note::

        The preferred way to obtain a connection is to use one of the
        convenience functions described in :ref:`websocket-clients` or
        :ref:`websocket-servers`. Instantiating a connection instance directly is
        tricky and is not recommended.

    This object has properties that expose connection metadata.

    .. autoattribute:: is_closed
    .. autoattribute:: close_reason
    .. autoattribute:: is_client
    .. autoattribute:: is_server
    .. autoattribute:: path
    .. autoattribute:: subprotocol

    A connection object has a pair of methods for sending and receiving
    WebSocket messages. Messages can be ``str`` or ``bytes`` objects.

    .. automethod:: send_message
    .. automethod:: get_message

    A connection object also has methods for sending pings and pongs. Each ping
    is sent with a unique payload, and the function blocks until a corresponding
    pong is received from the remote endpoint. This feature can be used to
    implement a bidirectional heartbeat.

    A pong, on the other hand, sends an unsolicited pong to the remote endpoint
    and does not expect or wait for a response. This feature can be used to
    implement a unidirectional heartbeat.

    .. automethod:: ping
    .. automethod:: pong

    Finally, the socket offers a method to close the connection. The connection
    context managers in :ref:`websocket-clients` and :ref:`websocket-servers`
    will automatically close the connection for you, but you may want to close
    the connection explicity if you are not using a context manager or if you
    want to customize the close reason.

    .. automethod:: aclose

.. autoclass:: CloseReason
    :members:

.. autoexception:: ConnectionClosed
