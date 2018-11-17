from collections import OrderedDict
from functools import partial
import itertools
import logging
import random
import ssl
import struct

from async_generator import async_generator, yield_, asynccontextmanager
from ipaddress import ip_address
import trio
import trio.abc
import trio.ssl
import wsproto.connection as wsconnection
import wsproto.frame_protocol as wsframeproto
from wsproto.events import BytesReceived
from yarl import URL

from .version import __version__


CONN_TIMEOUT = 60 # default connect & disconnect timeout, in seconds
MESSAGE_QUEUE_SIZE = 1
MAX_MESSAGE_SIZE = 2 ** 20 # 1 MiB
RECEIVE_BYTES = 4 * 2 ** 10 # 4 KiB
logger = logging.getLogger('trio-websocket')


@asynccontextmanager
@async_generator
async def open_websocket(host, port, resource, *, use_ssl, subprotocols=None,
    message_queue_size=MESSAGE_QUEUE_SIZE, max_message_size=MAX_MESSAGE_SIZE,
    connect_timeout=CONN_TIMEOUT, disconnect_timeout=CONN_TIMEOUT):
    '''
    Open a WebSocket client connection to a host.

    This async context manager connects when entering the context manager and
    disconnects when exiting. It yields a
    :class:`WebSocketConnection` instance.

    :param str host: The host to connect to.
    :param int port: The port to connect to.
    :param str resource: The resource, i.e. URL path.
    :param use_ssl: If this is an SSL context, then use that context. If this is
        ``True`` then use default SSL context. If this is ``False`` then disable
        SSL.
    :type use_ssl: bool or ssl.SSLContext
    :param subprotocols: An iterable of strings representing preferred
        subprotocols.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :param float connect_timeout: The number of seconds to wait for the
        connection before timing out.
    :param float disconnect_timeout: The number of seconds to wait when closing
        the connection before timing out.
    :raises trio.TooSlowError: if connecting or disconnecting times out.
    '''
    async with trio.open_nursery() as new_nursery:
        with trio.fail_after(connect_timeout):
            connection = await connect_websocket(new_nursery, host, port,
                resource, use_ssl=use_ssl, subprotocols=subprotocols,
                message_queue_size=message_queue_size,
                max_message_size=max_message_size)
        try:
            await yield_(connection)
        finally:
            with trio.fail_after(disconnect_timeout):
                await connection.aclose()


async def connect_websocket(nursery, host, port, resource, *, use_ssl,
    subprotocols=None, message_queue_size=MESSAGE_QUEUE_SIZE,
    max_message_size=MAX_MESSAGE_SIZE):
    '''
    Return an open WebSocket client connection to a host.

    This function is used to specify a custom nursery to run connection
    background tasks in. The caller is responsible for closing the connection.

    If you don't need a custom nursery, you should probably use
    :func:`open_websocket` instead.

    :param nursery: A Trio nursery to run background tasks in.
    :param str host: The host to connect to.
    :param int port: The port to connect to.
    :param str resource: The resource, i.e. URL path.
    :type use_ssl: bool or ssl.SSLContext
    :param subprotocols: An iterable of strings representing preferred
        subprotocols.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :rtype: WebSocketConnection
    '''
    if use_ssl == True:
        ssl_context = ssl.create_default_context()
    elif use_ssl == False:
        ssl_context = None
    elif isinstance(use_ssl, ssl.SSLContext):
        ssl_context = use_ssl
    else:
        raise TypeError('`use_ssl` argument must be bool or ssl.SSLContext')

    logger.debug('Connecting to ws%s://%s:%d%s',
        '' if ssl_context is None else 's', host, port, resource)
    if ssl_context is None:
        stream = await trio.open_tcp_stream(host, port)
    else:
        stream = await trio.open_ssl_over_tcp_stream(host, port,
            ssl_context=ssl_context, https_compatible=True)
    if port in (80, 443):
        host_header = host
    else:
        host_header = '{}:{}'.format(host, port)
    wsproto = wsconnection.WSConnection(wsconnection.CLIENT,
        host=host_header, resource=resource, subprotocols=subprotocols)
    connection = WebSocketConnection(stream, wsproto, path=resource,
        message_queue_size=message_queue_size,
        max_message_size=max_message_size)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    return connection


def open_websocket_url(url, ssl_context=None, *, subprotocols=None,
    message_queue_size=MESSAGE_QUEUE_SIZE, max_message_size=MAX_MESSAGE_SIZE,
    connect_timeout=CONN_TIMEOUT, disconnect_timeout=CONN_TIMEOUT):
    '''
    Open a WebSocket client connection to a URL.

    This async context manager connects when entering the context manager and
    disconnects when exiting. It yields a
    :class:`WebSocketConnection` instance.

    :param str url: A WebSocket URL, i.e. `ws:` or `wss:` URL scheme.
    :param ssl_context: Optional SSL context used for ``wss:`` URLs. A default
        SSL context is used for ``wss:`` if this argument is ``None``.
    :type ssl_context: ssl.SSLContext or None
    :param subprotocols: An iterable of strings representing preferred
        subprotocols.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :param float connect_timeout: The number of seconds to wait for the
        connection before timing out.
    :param float disconnect_timeout: The number of seconds to wait when closing
        the connection before timing out.
    :raises trio.TooSlowError: if connecting or disconnecting times out.
    '''
    host, port, resource, ssl_context = _url_to_host(url, ssl_context)
    return open_websocket(host, port, resource, use_ssl=ssl_context,
        subprotocols=subprotocols, message_queue_size=message_queue_size,
        max_message_size=max_message_size)


async def connect_websocket_url(nursery, url, ssl_context=None, *,
    subprotocols=None, message_queue_size=MESSAGE_QUEUE_SIZE,
    max_message_size=MAX_MESSAGE_SIZE):
    '''
    Return an open WebSocket client connection to a URL.

    This function is used to specify a custom nursery to run connection
    background tasks in. The caller is responsible for closing the connection.

    If you don't need a custom nursery, you should probably use
    :func:`open_websocket_url` instead.

    :param nursery: A nursery to run background tasks in.
    :param str url: A WebSocket URL.
    :param ssl_context: Optional SSL context used for ``wss:`` URLs.
    :type ssl_context: ssl.SSLContext or None
    :param subprotocols: An iterable of strings representing preferred
        subprotocols.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :rtype: WebSocketConnection
    '''
    host, port, resource, ssl_context = _url_to_host(url, ssl_context)
    return await connect_websocket(nursery, host, port, resource,
        use_ssl=ssl_context, subprotocols=subprotocols,
        message_queue_size=message_queue_size,
        max_message_size=max_message_size)


def _url_to_host(url, ssl_context):
    '''
    Convert a WebSocket URL to a (host,port,resource) tuple.

    The returned ``ssl_context`` is either the same object that was passed in,
    or if ``ssl_context`` is None, then a bool indicating if a default SSL
    context needs to be created.

    :param str url: A WebSocket URL.
    :type ssl_context: ssl.SSLContext or None
    :returns: A tuple of ``(host, port, resource, ssl_context)``.
    '''
    url = URL(url)
    if url.scheme not in ('ws', 'wss'):
        raise ValueError('WebSocket URL scheme must be "ws:" or "wss:"')
    if ssl_context is None:
        ssl_context = url.scheme == 'wss'
    elif url.scheme == 'ws':
        raise ValueError('SSL context must be None for ws: URL scheme')
    return url.host, url.port, url.path_qs, ssl_context


async def wrap_client_stream(nursery, stream, host, resource, *,
    subprotocols=None, message_queue_size=MESSAGE_QUEUE_SIZE,
    max_message_size=MAX_MESSAGE_SIZE):
    '''
    Wrap an arbitrary stream in a WebSocket connection.

    This is a low-level function only needed in rare cases. In most cases, you
    should use :func:`open_websocket` or :func:`open_websocket_url`.

    :param nursery: A Trio nursery to run background tasks in.
    :param stream: A Trio stream to be wrapped.
    :type stream: trio.abc.Stream
    :param str host: A host string that will be sent in the ``Host:`` header.
    :param str resource: A resource string, i.e. the path component to be
        accessed on the server.
    :param subprotocols: An iterable of strings representing preferred
        subprotocols.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :rtype: WebSocketConnection
    '''
    wsproto = wsconnection.WSConnection(wsconnection.CLIENT, host=host,
        resource=resource, subprotocols=subprotocols)
    connection = WebSocketConnection(stream, wsproto, path=resource,
        message_queue_size=message_queue_size,
        max_message_size=max_message_size)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    return connection


async def wrap_server_stream(nursery, stream,
    message_queue_size=MESSAGE_QUEUE_SIZE, max_message_size=MAX_MESSAGE_SIZE):
    '''
    Wrap an arbitrary stream in a server-side WebSocket.

    This is a low-level function only needed in rare cases. In most cases, you
    should use :func:`serve_websocket`.

    :param nursery: A nursery to run background tasks in.
    :param stream: A stream to be wrapped.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :type stream: trio.abc.Stream
    :rtype: WebSocketConnection
    '''
    wsproto = wsconnection.WSConnection(wsconnection.SERVER)
    connection = WebSocketConnection(stream, wsproto,
        message_queue_size=message_queue_size,
        max_message_size=max_message_size)
    nursery.start_soon(connection._reader_task)
    request = await connection._get_request()
    return request


async def serve_websocket(handler, host, port, ssl_context, *,
    handler_nursery=None, message_queue_size=MESSAGE_QUEUE_SIZE,
    max_message_size=MAX_MESSAGE_SIZE, connect_timeout=CONN_TIMEOUT,
    disconnect_timeout=CONN_TIMEOUT, task_status=trio.TASK_STATUS_IGNORED):
    '''
    Serve a WebSocket over TCP.

    This function supports the Trio nursery start protocol: ``server = await
    nursery.start(serve_websocket, …)``. It will block until the server
    is accepting connections and then return a :class:`WebSocketServer` object.

    Note that if ``host`` is ``None`` and ``port`` is zero, then you may get
    multiple listeners that have *different port numbers!*

    :param handler: An async function that is invoked with a request
        for each new connection.
    :param host: The host interface to bind. This can be an address of an
        interface, a name that resolves to an interface address (e.g.
        ``localhost``), or a wildcard address like ``0.0.0.0`` for IPv4 or
        ``::`` for IPv6. If ``None``, then all local interfaces are bound.
    :type host: str, bytes, or None
    :param int port: The port to bind to.
    :param ssl_context: The SSL context to use for encrypted connections, or
        ``None`` for unencrypted connection.
    :type ssl_context: ssl.SSLContext or None
    :param handler_nursery: An optional nursery to spawn handlers and background
        tasks in. If not specified, a new nursery will be created internally.
    :param int message_queue_size: The maximum number of messages that will be
        buffered in the library's internal message queue.
    :param int max_message_size: The maximum message size as measured by
        ``len()``. If a message is received that is larger than this size,
        then the connection is closed with code 1009 (Message Too Big).
    :param float connect_timeout: The number of seconds to wait for a client
        to finish connection handshake before timing out.
    :param float disconnect_timeout: The number of seconds to wait for a client
        to finish the closing handshake before timing out.
    :param task_status: Part of Trio nursery start protocol.
    :returns: This function runs until cancelled.
    '''
    if ssl_context is None:
        open_tcp_listeners = partial(trio.open_tcp_listeners, port, host=host)
    else:
        open_tcp_listeners = partial(trio.open_ssl_over_tcp_listeners, port,
            ssl_context, host=host, https_compatible=True)
    listeners = await open_tcp_listeners()
    server = WebSocketServer(handler, listeners,
        handler_nursery=handler_nursery, message_queue_size=message_queue_size,
        max_message_size=max_message_size, connect_timeout=connect_timeout,
        disconnect_timeout=disconnect_timeout)
    await server.run(task_status=task_status)


class ConnectionClosed(Exception):
    '''
    A WebSocket operation cannot be completed because the connection is closed
    or in the process of closing.
    '''
    def __init__(self, reason):
        '''
        Constructor.

        :param reason:
        :type reason: CloseReason
        '''
        self.reason = reason

    def __repr__(self):
        ''' Return representation. '''
        return '<{} {}>'.format(self.__class__.__name__, self.reason)


class CloseReason:
    ''' Contains information about why a WebSocket was closed. '''
    def __init__(self, code, reason):
        '''
        Constructor.

        :param int code:
        :param str reason:
        '''
        self._code = code
        try:
            self._name = wsframeproto.CloseReason(code).name
        except ValueError:
            if 1000 <= code <= 2999:
                self._name = 'RFC_RESERVED'
            elif 3000 <= code <= 3999:
                self._name = 'IANA_RESERVED'
            elif 4000 <= code <= 4999:
                self._name = 'PRIVATE_RESERVED'
            else:
                self._name = 'INVALID_CODE'
        self._reason = reason

    @property
    def code(self):
        ''' (Read-only) The numeric close code. '''
        return self._code

    @property
    def name(self):
        ''' (Read-only) The human-readable close code. '''
        return self._name

    @property
    def reason(self):
        ''' (Read-only) An arbitrary reason string. '''
        return self._reason

    def __repr__(self):
        ''' Show close code, name, and reason. '''
        return '<{} code={} name={} reason={}>'.format(self.__class__.__name__,
            self.code, self.name, self.reason)


class Future:
    ''' Represents a value that will be available in the future. '''
    def __init__(self):
        ''' Constructor. '''
        self._value = None
        self._value_event = trio.Event()

    def set_value(self, value):
        '''
        Set a value, which will notify any waiters.

        :param value:
        '''
        self._value = value
        self._value_event.set()

    async def wait_value(self):
        '''
        Wait for this future to have a value, then return it.

        :returns: The value set by ``set_value()``.
        '''
        await self._value_event.wait()
        return self._value


class WebSocketRequest:
    '''
    Represents a handshake presented by a client to a server.

    The server may modify the handshake or leave it as is. The server should
    call ``accept()`` to finish the handshake and obtain a connection object.
    '''
    def __init__(self, accept_fn, event):
        '''
        Constructor.

        :param accept_fn: A function to call that will finish the handshake and
            return a ``WebSocketSconnection``.
        :type event: wsproto.events.ConnectionRequested
        '''
        self._accept_fn = accept_fn
        self._event = event
        self._subprotocol = None

    @property
    def headers(self):
        '''
        (Read-only) HTTP headers represented as a list of
        (name, value) pairs.

        :rtype: list[tuple]
        '''
        return self._event.h11request.headers

    @property
    def proposed_subprotocols(self):
        '''
        (Read-only) A tuple of protocols proposed by the client.

        :rtype: tuple[str]
        '''
        return tuple(self._event.proposed_subprotocols)

    @property
    def subprotocol(self):
        '''
        (Read/Write) The selected protocol. Defaults to ``None``.

        :rtype: str or None
        '''
        return self._subprotocol

    @subprotocol.setter
    def subprotocol(self, value):
        '''
        Set the selected protocol.

        :type value: str or None
        '''
        self._subprotocol = value

    @property
    def url(self):
        '''
        (Read-only) The requested URL. Typically this URL does not contain a
        scheme, host, or port.

        :rtype: yarl.URL
        '''
        return URL(self._event.h11request.target.decode('ascii'))


    async def accept(self):
        '''
        Finish the handshake with the terms contained in this request and
        return a connection object.

        :rtype: WebSocketConnection
        '''
        return await self._accept_fn(self)


class WebSocketConnection(trio.abc.AsyncResource):
    ''' A WebSocket connection. '''

    CONNECTION_ID = itertools.count()

    def __init__(self, stream, wsproto, *, path=None,
        message_queue_size=MESSAGE_QUEUE_SIZE,
        max_message_size=MAX_MESSAGE_SIZE):
        '''
        Constructor.

        Generally speaking, users are discouraged from directly instantiating a
        ``WebSocketConnection`` and should instead use one of the convenience
        functions in this module, e.g. ``open_websocket()`` or
        ``serve_websocket()``. This class has some tricky internal logic and
        timing that depends on whether it is an instance of a client connection
        or a server connection. The convenience functions handle this complexity
        for you.

        :param SocketStream stream:
        :type wsproto: wsproto.connection.WSConnection
        :param str path: The URL path for this connection. Only used for server
            instances.
        :param int message_queue_size: The maximum number of messages that will be
            buffered in the library's internal message queue.
        :param int max_message_size: The maximum message size as measured by
            ``len()``. If a message is received that is larger than this size,
            then the connection is closed with code 1009 (Message Too Big).
        '''
        self._close_reason = None
        self._id = next(self.__class__.CONNECTION_ID)
        self._stream = stream
        self._stream_lock = trio.StrictFIFOLock()
        self._wsproto = wsproto
        self._message_size = 0
        self._message_parts = []  # type: List[bytes|str]
        self._max_message_size = max_message_size
        self._reader_running = True
        self._path = path
        self._subprotocol = None
        self._send_channel, self._recv_channel = trio.open_memory_channel(
            message_queue_size)
        self._pings = OrderedDict()
        # Set when the server has received a connection request event. This
        # future is never set on client connections.
        self._connection_proposal = Future()
        # Set once the WebSocket open handshake takes place, i.e.
        # ConnectionRequested for server or ConnectedEstablished for client.
        self._open_handshake = trio.Event()
        # Set once a WebSocket closed handshake takes place, i.e after a close
        # frame has been sent and a close frame has been received.
        self._close_handshake = trio.Event()

    @property
    def closed(self):
        '''
        (Read-only) The reason why the connection was closed, or ``None`` if the
        connection is still open.

        :rtype: CloseReason
        '''
        return self._close_reason

    @property
    def is_client(self):
        ''' (Read-only) Is this a client instance? '''
        return self._wsproto.client

    @property
    def is_server(self):
        ''' (Read-only) Is this a server instance? '''
        return not self._wsproto.client

    @property
    def path(self):
        ''' (Read-only) The path from the HTTP handshake. '''
        return self._path

    @property
    def subprotocol(self):
        '''
        (Read-only) The negotiated subprotocol, or ``None`` if there is no
        subprotocol.

        This is only valid after the opening handshake is complete.

        :rtype: str or None
        '''
        return self._subprotocol

    async def aclose(self, code=1000, reason=None):
        '''
        Close the WebSocket connection.

        This sends a closing frame and suspends until the connection is closed.
        After calling this method, any further I/O on this WebSocket (such as
        ``get_message()`` or ``send_message()``) will raise
        ``ConnectionClosed``.

        This method is idempotent: it may be called multiple times on the same
        connection without any errors.

        :param int code: A 4-digit code number indicating the type of closure.
        :param str reason: An optional string describing the closure.
        '''
        if self._close_reason:
            # Per AsyncResource interface, calling aclose() on a closed resource
            # should succeed.
            return
        # Wsproto will throw an AttributeError if you close it during the
        # handshake phase. This is an open bug:
        # https://github.com/python-hyper/wsproto/issues/59
        try:
            self._wsproto.close(code=code, reason=reason)
        except AttributeError:
            pass
        try:
            await self._recv_channel.aclose()
            await self._write_pending()
            await self._close_handshake.wait()
        finally:
            # If cancelled during WebSocket close, make sure that the underlying
            # stream is closed.
            await self._close_stream()


    async def get_message(self):
        '''
        Receive the next WebSocket message.

        If no message is available immediately, then this function blocks until
        a message is ready.

        If the remote endpoint closes the connection, then the caller can still
        get messages sent prior to closing. Once all pending messages have been
        retrieved, additional calls to this method will raise
        ``ConnectionClosed``. If the local endpoint closes the connection, then
        pending messages are discarded and calls to this method will immediately
        raise ``ConnectionClosed``.

        :rtype: str or bytes
        :raises ConnectionClosed: if the connection is closed.
        '''
        try:
            message = await self._recv_channel.receive()
        except (trio.ClosedResourceError, trio.EndOfChannel):
            raise ConnectionClosed(self._close_reason) from None
        return message

    async def ping(self, payload=None):
        '''
        Send WebSocket ping to remote endpoint and wait for a correspoding pong.

        Each in-flight ping must include a unique payload. This function sends
        the ping and then waits for a corresponding pong from the remote
        endpoint.

        *Note: If the remote endpoint recieves multiple pings, it is allowed to
        send a single pong. Therefore, the order of calls to ``ping()`` is
        tracked, and a pong will wake up its corresponding ping as well as all
        previous in-flight pings.*

        :param payload: The payload to send. If ``None`` then a random 32-bit
            payload is created.
        :type payload: bytes or None
        :raises ConnectionClosed: if connection is closed.
        :raises ValueError: if ``payload`` is identical to another in-flight
            ping.
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        if payload in self._pings:
            raise ValueError('Payload value {} is already in flight.'.
                format(payload))
        if payload is None:
            payload = struct.pack('!I', random.getrandbits(32))
        event = trio.Event()
        self._pings[payload] = event
        self._wsproto.ping(payload)
        await self._write_pending()
        await event.wait()

    async def pong(self, payload=None):
        '''
        Send an unsolicted pong.

        :param payload: The pong's payload. If ``None``, then no payload is
            sent.
        :type payload: bytes or None
        :raises ConnectionClosed: if connection is closed
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        self._wsproto.ping(payload)
        await self._write_pending()

    async def send_message(self, message):
        '''
        Send a WebSocket message.

        :param message: The message to send.
        :type message: str or bytes
        :raises ConnectionClosed: if connection is already closed.
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        self._wsproto.send_data(message)
        await self._write_pending()

    def __str__(self):
        ''' Connection ID and type. '''
        type_ = 'client' if self.is_client else 'server'
        return '{}-{}'.format(type_, self._id)

    async def _abort_web_socket(self):
        '''
        If a stream is closed outside of this class, e.g. due to network
        conditions or because some other code closed our stream object, then we
        cannot perform the close handshake. We just need to clean up internal
        state.
        '''
        close_reason = wsframeproto.CloseReason.ABNORMAL_CLOSURE
        if not self._wsproto.closed:
            self._wsproto.close(close_reason)
        if self._close_reason is None:
            await self._close_web_socket(close_reason)
        self._reader_running = False
        # We didn't really handshake, but we want any task waiting on this event
        # (e.g. self.aclose()) to resume.
        self._close_handshake.set()

    async def _accept(self, proposal):
        '''
        Accept a given proposal.

        This finishes the server-side handshake with the given proposal
        attributes and return the connection instance.

        :rtype: WebSocketConnection
        '''
        self._subprotocol = proposal.subprotocol
        self._path = proposal.url.path
        self._wsproto.accept(proposal._event, self._subprotocol)
        await self._write_pending()
        self._open_handshake.set()
        return self

    async def _close_stream(self):
        ''' Close the TCP connection. '''
        self._reader_running = False
        try:
            await self._stream.aclose()
        except trio.BrokenResourceError:
            # This means the TCP connection is already dead.
            pass

    async def _close_web_socket(self, code, reason=None):
        '''
        Mark the WebSocket as closed. Close the message channel so that if any
        tasks are suspended in get_message(), they will wake up with a
        ConnectionClosed exception.
        '''
        self._close_reason = CloseReason(code, reason)
        exc = ConnectionClosed(self._close_reason)
        logger.debug('%s websocket closed %r', self, exc)
        await self._send_channel.aclose()

    async def _get_request(self):
        '''
        Return a proposal for a WebSocket handshake.

        This method can only be called on server connections and it may only be
        called one time.

        :rtype: WebSocketRequest
        '''
        if not self.is_server:
            raise Exception('This method is only valid for server connections.')
        if self._connection_proposal is None:
            raise Exception('No proposal available. Did you call this method'
                ' multiple times or at the wrong time?')
        proposal = await self._connection_proposal.wait_value()
        self._connection_proposal = None
        return proposal

    async def _handle_connection_requested_event(self, event):
        '''
        Handle a ConnectionRequested event.

        This method is async even though it never awaits, because the event
        dispatch requires an async function.

        :param event:
        '''
        proposal = WebSocketRequest(self._accept, event)
        self._connection_proposal.set_value(proposal)

    async def _handle_connection_established_event(self, event):
        '''
        Handle a ConnectionEstablished event.

        :param event:
        '''
        self._subprotocol = event.subprotocol
        self._open_handshake.set()

    async def _handle_connection_closed_event(self, event):
        '''
        Handle a ConnectionClosed event.

        :param event:
        '''
        await self._write_pending()
        await self._close_web_socket(event.code, event.reason or None)
        self._close_handshake.set()

    async def _handle_connection_failed_event(self, event):
        '''
        Handle a ConnectionFailed event.

        :param event:
        '''
        await self._write_pending()
        await self._close_web_socket(event.code, event.reason or None)
        await self._close_stream()
        self._open_handshake.set()
        self._close_handshake.set()

    async def _handle_data_received_event(self, event):
        '''
        Handle a BytesReceived or TextReceived event.

        :param event:
        '''
        self._message_size += len(event.data)
        self._message_parts.append(event.data)
        if self._message_size > self._max_message_size:
            err = 'Exceeded maximum message size: {} bytes'.format(
                self._max_message_size)
            self._message_size = 0
            self._message_parts = []
            self._close_reason = CloseReason(1009, err)
            self._wsproto.close(code=1009, reason=err)
            await self._write_pending()
            await self._recv_channel.aclose()
            self._reader_running = False
        elif event.message_finished:
            msg = (b'' if isinstance(event, BytesReceived) else '') \
                .join(self._message_parts)
            self._message_size = 0
            self._message_parts = []
            try:
                await self._send_channel.send(msg)
            except trio.BrokenResourceError:
                # The receive channel is closed, probably because somebody
                # called ``aclose()``. We don't want to abort the reader task,
                # and there's no useful cleanup that we can do here.
                pass

    async def _handle_ping_received_event(self, event):
        '''
        Handle a PingReceived event.

        Wsproto queues a pong frame automatically, so this handler just needs to
        send it.

        :param event:
        '''
        logger.debug('%s ping %r', self, event.payload)
        await self._write_pending()

    async def _handle_pong_received_event(self, event):
        '''
        Handle a PongReceived event.

        When a pong is received, check if we have any ping requests waiting for
        this pong response. If the remote endpoint skipped any earlier pings,
        then we wake up those skipped pings, too.

        This function is async even though it never awaits, because the other
        event handlers are async, too, and event dispatch would be more
        complicated if some handlers were sync.

        :param event:
        '''
        payload = bytes(event.payload)
        try:
            event = self._pings[payload]
        except KeyError:
            # We received a pong that doesn't match any in-flight pongs. Nothing
            # we can do with it, so ignore it.
            return
        while self._pings:
            key, event = self._pings.popitem(0)
            skipped = ' [skipped] ' if payload != key else ' '
            logger.debug('%s pong%s%r', self, skipped, key)
            event.set()
            if payload == key:
                break

    async def _reader_task(self):
        ''' A background task that reads network data and generates events. '''
        handlers = {
            'ConnectionRequested': self._handle_connection_requested_event,
            'ConnectionFailed': self._handle_connection_failed_event,
            'ConnectionEstablished': self._handle_connection_established_event,
            'ConnectionClosed': self._handle_connection_closed_event,
            'BytesReceived': self._handle_data_received_event,
            'TextReceived': self._handle_data_received_event,
            'PingReceived': self._handle_ping_received_event,
            'PongReceived': self._handle_pong_received_event,
        }

        if self.is_client:
            # Clients need to initiate the negotiation:
            await self._write_pending()

        while self._reader_running:
            # Process events.
            for event in self._wsproto.events():
                event_type = type(event).__name__
                try:
                    handler = handlers[event_type]
                    logger.debug('%s received event: %s', self,
                        event_type)
                    await handler(event)
                except KeyError:
                    logger.warning('%s received unknown event type: "%s"', self,
                        event_type)

            # Get network data.
            try:
                data = await self._stream.receive_some(RECEIVE_BYTES)
            except (trio.BrokenResourceError, trio.ClosedResourceError):
                await self._abort_web_socket()
                break
            if len(data) == 0:
                logger.debug('%s received zero bytes (connection closed)',
                    self)
                # If TCP closed before WebSocket, then record it as an abnormal
                # closure.
                if not self._wsproto.closed:
                    await self._abort_web_socket()
                break
            else:
                logger.debug('%s received %d bytes', self, len(data))
                if not self._wsproto.closed:
                    self._wsproto.receive_bytes(data)

        logger.debug('%s reader task finished', self)

    async def _write_pending(self):
        ''' Write any pending protocol data to the network socket. '''
        data = self._wsproto.bytes_to_send()
        if len(data) > 0:
            # The reader task and one or more writers might try to send messages
            # at the same time, so we need to synchronize access to this stream.
            async with self._stream_lock:
                logger.debug('%s sending %d bytes', self, len(data))
                try:
                    await self._stream.send_all(data)
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    await self._abort_web_socket()
                    raise ConnectionClosed(self._close_reason) from None
        else:
            logger.debug('%s no pending data to send', self)


class ListenPort:
    ''' Represents a listener on a given address and port. '''
    def __init__(self, address, port, is_ssl):
        self.address = ip_address(address)
        self.port = port
        self.is_ssl = is_ssl

    def __str__(self):
        ''' Return a compact representation, like 127.0.0.1:80 or [::1]:80. '''
        scheme = 'wss' if self.is_ssl else 'ws'
        if self.address.version == 4:
            return '{}://{}:{}'.format(scheme, self.address, self.port)
        else:
            return '{}://[{}]:{}'.format(scheme, self.address, self.port)


class WebSocketServer:
    '''
    WebSocket server.

    The server class handles incoming connections on one or more ``Listener``
    objects. For each incoming connection, it creates a ``WebSocketConnection``
    instance and starts some background tasks,
    '''

    def __init__(self, handler, listeners, *, handler_nursery=None,
        message_queue_size=MESSAGE_QUEUE_SIZE,
        max_message_size=MAX_MESSAGE_SIZE, connect_timeout=CONN_TIMEOUT,
        disconnect_timeout=CONN_TIMEOUT):
        '''
        Constructor.

        Note that if ``host`` is ``None`` and ``port`` is zero, then you may get
        multiple listeners that have _different port numbers!_ See the
        ``listeners`` property.

        :param handler: the async function called with the corresponding
            WebSocketConnection on each new connection.  The call will be made
            once the HTTP handshake completes, which notably implies that the
            connection's `path` property will be valid.
        :param listeners: The WebSocket will be served on each of the listeners.
        :param handler_nursery: An optional nursery to spawn connection tasks
            inside of. If ``None``, then a new nursery will be created
            internally.
        :param float connect_timeout: The number of seconds to wait for a client
            to finish connection handshake before timing out.
        :param float disconnect_timeout: The number of seconds to wait for a client
            to finish the closing handshake before timing out.
        '''
        if len(listeners) == 0:
            raise ValueError('Listeners must contain at least one item.')
        self._handler = handler
        self._handler_nursery = handler_nursery
        self._listeners = listeners
        self._message_queue_size = message_queue_size
        self._max_message_size = max_message_size
        self._connect_timeout = connect_timeout
        self._disconnect_timeout = disconnect_timeout

    @property
    def port(self):
        """Returns the requested or kernel-assigned port number.

        In the case of kernel-assigned port (requested with port=0 in the
        constructor), the assigned port will be reflected after calling
        starting the `listen` task.  (Technically, once listen reaches the
        "started" state.)

        This property only works if you have a single listener, and that
        listener must be socket-based.
        """
        if len(self._listeners) > 1:
            raise RuntimeError('Cannot get port because this server has'
                ' more than 1 listeners.')
        try:
            listener = self.listeners[0]
            return listener.port
        except AttributeError:
            raise RuntimeError('This socket does not have a port: {!r}'
                .format(listener)) from None

    @property
    def listeners(self):
        '''
        Return a list of listener metadata. Each TCP listener is represented as
        a ``ListenPort`` instance. Other listener types are represented by their
        ``repr()``.
        '''
        listeners = list()
        for listener in self._listeners:
            if isinstance(listener, trio.SocketListener):
                socket = listener.socket
                is_ssl = False
            elif isinstance(listener, trio.ssl.SSLListener):
                socket = listener.transport_listener.socket
                is_ssl = True
            else:
                socket = None
            if socket:
                sockname = socket.getsockname()
                listeners.append(ListenPort(sockname[0], sockname[1], is_ssl))
            else:
                listeners.append(repr(listener))
        return listeners

    async def run(self, *, task_status=trio.TASK_STATUS_IGNORED):
        '''
        Start serving incoming connections requests.

        This method supports the Trio nursery start protocol: ``await
        nursery.start(server.run, …)``. This ensures that the server is ready
        to accept connections.

        :param task_status: Part of the Trio nursery start protocol.
        :returns: This method never returns unless cancelled.
        '''
        async with trio.open_nursery() as nursery:
            serve_listeners = partial(trio.serve_listeners,
                self._handle_connection, self._listeners,
                handler_nursery=self._handler_nursery)
            await nursery.start(serve_listeners)
            logger.debug('Listening on %s',
                ','.join([str(l) for l in self.listeners]))
            task_status.started(self)
            await trio.sleep_forever()

    async def _handle_connection(self, stream):
        '''
        Handle an incoming connection by spawning a connection background task
        and a handler task inside a new nursery.

        :param stream:
        :type stream: trio.abc.Stream
        '''
        async with trio.open_nursery() as nursery:
            wsproto = wsconnection.WSConnection(wsconnection.SERVER)
            connection = WebSocketConnection(stream, wsproto,
                message_queue_size=self._message_queue_size,
                max_message_size=self._max_message_size)
            nursery.start_soon(connection._reader_task)
            with trio.move_on_after(self._connect_timeout) as connect_scope:
                request = await connection._get_request()
            if connect_scope.cancelled_caught:
                nursery.cancel_scope.cancel()
                await stream.aclose()
                return
            try:
                await self._handler(request)
            finally:
                with trio.move_on_after(self._disconnect_timeout):
                    # aclose() will shut down the reader task even if its
                    # cancelled:
                    await connection.aclose()
