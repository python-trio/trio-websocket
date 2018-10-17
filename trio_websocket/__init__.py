import itertools
import logging
import ssl
from functools import partial

from async_generator import async_generator, yield_, asynccontextmanager
import attr
from ipaddress import ip_address
import trio
import trio.abc
import trio.ssl
import wsproto.connection as wsconnection
import wsproto.frame_protocol as wsframeproto
from yarl import URL

from ._channel import open_channel, EndOfChannel
from .version import __version__

RECEIVE_BYTES = 4096
logger = logging.getLogger('trio-websocket')


@asynccontextmanager
@async_generator
async def open_websocket(host, port, resource, use_ssl):
    '''
    Open a WebSocket client connection to a host.

    This function is an async context manager that connects before entering the
    context manager and disconnects after leaving. It yields a
    `WebSocketConnection` instance.

    ``use_ssl`` can be an ``SSLContext`` object, or if it's ``True``, then a
    default SSL context will be created. If it is ``False``, then SSL will not
    be used at all.

    :param str host: the host to connect to
    :param int port: the port to connect to
    :param str resource: the resource a.k.a. path
    :param use_ssl: a bool or SSLContext
    '''
    async with trio.open_nursery() as new_nursery:
        connection = await connect_websocket(new_nursery, host, port, resource,
            use_ssl)
        async with connection:
            await yield_(connection)


async def connect_websocket(nursery, host, port, resource, use_ssl):
    '''
    Return a WebSocket client connection to a host.

    Most users should use ``open_websocket(…)`` instead of this function. This
    function is not an async context manager, and it requires a nursery argument
    for the connection's background task[s]. The caller is responsible for
    closing the connection.

    :param nursery: a Trio nursery to run background tasks in
    :param str host: the host to connect to
    :param int port: the port to connect to
    :param str resource: the resource a.k.a. path
    :param use_ssl: a bool or SSLContext
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
        host=host_header, resource=resource)
    connection = WebSocketConnection(stream, wsproto, path=resource)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    return connection


def open_websocket_url(url, ssl_context=None):
    '''
    Open a WebSocket client connection to a URL.

    This function is an async context manager that connects when entering the
    context manager and disconnects when exiting. It yields a
    `WebSocketConnection` instance.

    If ``ssl_context`` is ``None`` and the URL scheme is ``wss:``, then a
    default SSL context will be created. It is an error to pass an SSL context
    for ``ws:`` URLs.

    :param str url: a WebSocket URL
    :param ssl_context: optional ``SSLContext`` used for ``wss:`` URLs
    '''
    host, port, resource, ssl_context = _url_to_host(url, ssl_context)
    return open_websocket(host, port, resource, ssl_context)


async def connect_websocket_url(nursery, url, ssl_context=None):
    '''
    Return a WebSocket client connection to a URL.

    Most users should use ``open_websocket_url(…)`` instead of this function.
    This function is not an async context manager, and it requires a nursery
    argument for the connection's background task[s].

    If ``ssl_context`` is ``None`` and the URL scheme is ``wss:``, then a
    default SSL context will be created. It is an error to pass an SSL context
    for ``ws:`` URLs.

    :param str url: a WebSocket URL
    :param ssl_context: optional ``SSLContext`` used for ``wss:`` URLs
    :param nursery: a Trio nursery to run background tasks in
    :rtype: WebSocketConnection
    '''
    host, port, resource, ssl_context = _url_to_host(url, ssl_context)
    return await connect_websocket(nursery, host, port, resource, ssl_context)


def _url_to_host(url, ssl_context):
    '''
    Convert a WebSocket URL to a (host,port,resource) tuple.

    The returned ``ssl_context`` is either the same object that was passed in,
    or if ``ssl_context`` is None, then a bool indicating if a default SSL
    context needs to be created.

    :param str url: a WebSocket URL
    :param ssl_context: ``SSLContext`` or ``None``
    :return: tuple of ``(host, port, resource, ssl_context)``
    '''
    url = URL(url)
    if url.scheme not in ('ws', 'wss'):
        raise ValueError('WebSocket URL scheme must be "ws:" or "wss:"')
    if ssl_context is None:
        ssl_context = url.scheme == 'wss'
    elif url.scheme == 'ws':
        raise ValueError('SSL context must be None for ws: URL scheme')
    resource = '{}?{}'.format(url.path, url.query_string)
    return url.host, url.port, resource, ssl_context


async def wrap_client_stream(nursery, stream, host, resource):
    '''
    Wrap an arbitrary stream in a client-side ``WebSocketConnection``.

    This is a low-level function only needed in rare cases. Most users should
    call ``open_websocket()`` or ``open_websocket_url()``.

    :param nursery: A Trio nursery to run background tasks in.
    :param stream: A Trio stream to be wrapped.
    :param str host: A host string that will be sent in the ``Host:`` header.
    :param str resource: A resource string, i.e. the path component to be
        accessed on the server.
    :rtype: WebSocketConnection
    '''
    wsproto = wsconnection.WSConnection(wsconnection.CLIENT, host=host,
        resource=resource)
    connection = WebSocketConnection(stream, wsproto, path=resource)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    return connection


async def wrap_server_stream(nursery, stream):
    '''
    Wrap an arbitrary stream in a server-side ``WebSocketConnection``.

    This is a low-level function only needed in rare cases. Most users should
    call ``serve_websocket()`.

    :param nursery: A Trio nursery to run background tasks in.
    :param stream: A Trio stream to be wrapped.
    :param task_status: part of Trio nursery start protocol
    :rtype: WebSocketConnection
    '''
    wsproto = wsconnection.WSConnection(wsconnection.SERVER)
    connection = WebSocketConnection(stream, wsproto)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    return connection


async def serve_websocket(handler, host, port, ssl_context, *,
    handler_nursery=None, task_status=trio.TASK_STATUS_IGNORED):
    '''
    Serve a WebSocket over TCP.

    This function supports the Trio nursery start protocol: ``server = await
    nursery.start(serve_websocket, …)``. It will block until the server
    is accepting connections and then return the WebSocketServer object.

    Note that if ``host`` is ``None`` and ``port`` is zero, then you may get
    multiple listeners that have _different port numbers!_

    :param handler: The async function called with the corresponding
        WebSocketConnection on each new connection.  The call will be made
        once the HTTP handshake completes, which notably implies that the
        connection's `path` property will be valid.
    :param host: The host interface to bind. This can be an address of an
        interface, a name that resolves to an interface address (e.g.
        ``localhost``), or a wildcard address like ``0.0.0.0`` for IPv4 or
        ``::`` for IPv6. If ``None``, then all local interfaces are bound.
    :type host: str, bytes, or None
    :param int port: The port to bind to
    :param ssl_context: The SSL context to use for encrypted connections, or
        ``None`` for unencrypted connection.
    :type ssl_context: SSLContext or None
    :param handler_nursery: An optional nursery to spawn handlers and background
        tasks in. If not specified, a new nursery will be created internally.
    :param task_status: part of Trio nursery start protocol
    :returns: This function never returns unless cancelled.
    '''
    if ssl_context is None:
        open_tcp_listeners = partial(trio.open_tcp_listeners, port, host=host)
    else:
        open_tcp_listeners = partial(trio.open_ssl_over_tcp_listeners, port,
            ssl_context, host=host, https_compatible=True)
    listeners = await open_tcp_listeners()
    server = WebSocketServer(handler, listeners,
        handler_nursery=handler_nursery)
    await server.run(task_status=task_status)


class ConnectionClosed(Exception):
    '''
    A WebSocket operation cannot be completed because the connection is closed
    or in the process of closing.
    '''
    def __init__(self, reason):
        '''
        Constructor.

        :param CloseReason reason:
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
        ''' The numeric close code. '''
        return self._code

    @property
    def name(self):
        ''' The human-readable close code. '''
        return self._name

    @property
    def reason(self):
        ''' An arbitrary reason string. '''
        return self._reason

    def __repr__(self):
        ''' Show close code, name, and reason. '''
        return '<{} code={} name={} reason={}>'.format(self.__class__.__name__,
            self.code, self.name, self.reason)


class WebSocketConnection(trio.abc.AsyncResource):
    ''' A WebSocket connection. '''

    CONNECTION_ID = itertools.count()

    def __init__(self, stream, wsproto, path=None):
        '''
        Constructor.

        :param SocketStream stream:
        :param wsproto: a WSConnection instance
        :param client: a Trio cancel scope (only used by the server)
        '''
        self._close_reason = None
        self._id = next(self.__class__.CONNECTION_ID)
        self._stream = stream
        self._stream_lock = trio.StrictFIFOLock()
        self._wsproto = wsproto
        self._bytes_message = b''
        self._str_message = ''
        self._reader_running = True
        self._path = path
        self._put_channel, self._get_channel = open_channel(0)
        # Set once the WebSocket open handshake takes place, i.e.
        # ConnectionRequested for server or ConnectedEstablished for client.
        self._open_handshake = trio.Event()
        # Set once a WebSocket closed handshake takes place, i.e after a close
        # frame has been sent and a close frame has been received.
        self._close_handshake = trio.Event()

    @property
    def close_reason(self):
        '''
        A ``CloseReason`` object indicating why the connection was closed.
        '''
        return self._close_reason

    @property
    def is_closed(self):
        ''' A boolean indicating whether the WebSocket is closed. '''
        return self._close_reason is not None

    @property
    def is_client(self):
        ''' Is this a client instance? '''
        return self._wsproto.client

    @property
    def is_server(self):
        ''' Is this a server instance? '''
        return not self._wsproto.client

    @property
    def path(self):
        """Returns the path from the HTTP handshake."""
        return self._path

    async def aclose(self, code=1000, reason=None):
        '''
        Close the WebSocket connection.

        This sends a closing frame and suspends until the connection is closed.
        After calling this method, any futher I/O on this WebSocket (such as
        ``get_message()`` or ``send_message()``) will raise
        ``ConnectionClosed``.

        :param int code:
        :param str reason:
        :raises ConnectionClosed: if connection is already closed
        '''
        if self._close_reason:
            # Per AsyncResource interface, calling aclose() on a closed resource
            # should succeed.
            return
        self._wsproto.close(code=code, reason=reason)
        try:
            await self._write_pending()
            await self._close_handshake.wait()
        finally:
            # If cancelled during WebSocket close, make sure that the underlying
            # stream is closed.
            await self._close_stream()

    async def get_message(self):
        '''
        Return the next WebSocket message.

        Suspends until a message is available. Raises ``ConnectionClosed`` if
        the connection is already closed or closes while waiting for a message.

        :return: str or bytes
        :raises ConnectionClosed: if connection is closed
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        try:
            message = await self._get_channel.get()
        except EndOfChannel:
            raise ConnectionClosed(self._close_reason) from None
        return message

    async def ping(self, payload):
        '''
        Send WebSocket ping to peer.

        Does not wait for pong reply. (Is this the right behavior? This may
        change in the future.) Raises ``ConnectionClosed`` if the connection is
        closed.

        :param payload: str or bytes payloads
        :raises ConnectionClosed: if connection is closed
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        self._wsproto.ping(payload)
        await self._write_pending()

    async def send_message(self, message):
        '''
        Send a WebSocket message.

        Raises ``ConnectionClosed`` if the connection is closed..

        :param message: str or bytes
        :raises ConnectionClosed: if connection is closed
        '''
        if self._close_reason:
            raise ConnectionClosed(self._close_reason)
        self._wsproto.send_data(message)
        await self._write_pending()

    def _abort_web_socket(self):
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
            self._close_web_socket(close_reason)
        self._reader_running = False
        # We didn't really handshake, but we want any task waiting on this event
        # (e.g. self.aclose()) to resume.
        self._close_handshake.set()

    async def _close_stream(self):
        ''' Close the TCP connection. '''
        self._reader_running = False
        try:
            await self._stream.aclose()
        except trio.BrokenResourceError:
            # This means the TCP connection is already dead.
            pass

    def _close_web_socket(self, code, reason=None):
        '''
        Mark the WebSocket as closed. Close the message channel so that if any
        tasks are suspended in get_message(), they will wake up with a
        ConnectionClosed exception.
        '''
        self._close_reason = CloseReason(code, reason)
        exc = ConnectionClosed(self._close_reason)
        logger.debug('conn#%d websocket closed %r', self._id, exc)
        self._put_channel.close()

    async def _handle_connection_requested_event(self, event):
        '''
        Handle a ConnectionRequested event.

        :param event:
        '''
        self._path = event.h11request.target
        self._wsproto.accept(event)
        await self._write_pending()
        self._open_handshake.set()

    async def _handle_connection_established_event(self, event):
        '''
        Handle a ConnectionEstablished event.

        :param event:
        '''
        self._open_handshake.set()

    async def _handle_connection_closed_event(self, event):
        '''
        Handle a ConnectionClosed event.

        :param event:
        '''
        await self._write_pending()
        self._close_web_socket(event.code, event.reason or None)
        self._close_handshake.set()

    async def _handle_connection_failed_event(self, event):
        '''
        Handle a ConnectionFailed event.

        :param event:
        '''
        await self._write_pending()
        self._close_web_socket(event.code, event.reason or None)
        await self._close_stream()
        self._open_handshake.set()
        self._close_handshake.set()

    async def _handle_bytes_received_event(self, event):
        '''
        Handle a BytesReceived event.

        :param event:
        '''
        self._bytes_message += event.data
        if event.message_finished:
            await self._put_channel.put(self._bytes_message)
            self._bytes_message = b''

    async def _handle_text_received_event(self, event):
        '''
        Handle a TextReceived event.

        :param event:
        '''
        self._str_message += event.data
        if event.message_finished:
            await self._put_channel.put(self._str_message)
            self._str_message = ''

    async def _handle_ping_received_event(self, event):
        '''
        Handle a PingReceived event.

        Wsproto queues a pong frame automatically, so this handler just needs to
        send it.

        :param event:
        '''
        await self._write_pending()

    async def _handle_pong_received_event(self, event):
        '''
        Handle a PongReceived event.

        Currently we don't do anything special for a Pong frame, but this may
        change in the future. This handler is here as a placeholder.

        :param event:
        '''
        logger.debug('conn#%d pong %r', self._id, event.payload)

    async def _reader_task(self):
        ''' A background task that reads network data and generates events. '''
        handlers = {
            'ConnectionRequested': self._handle_connection_requested_event,
            'ConnectionFailed': self._handle_connection_failed_event,
            'ConnectionEstablished': self._handle_connection_established_event,
            'ConnectionClosed': self._handle_connection_closed_event,
            'BytesReceived': self._handle_bytes_received_event,
            'TextReceived': self._handle_text_received_event,
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
                    logger.debug('conn#%d received event: %s', self._id,
                        event_type)
                    await handler(event)
                except KeyError:
                    logger.warning('Received unknown event type: %s',
                        event_type)

            # Get network data.
            try:
                data = await self._stream.receive_some(RECEIVE_BYTES)
            except (trio.BrokenResourceError, trio.ClosedResourceError):
                self._abort_web_socket()
                break
            if len(data) == 0:
                logger.debug('conn#%d received zero bytes (connection closed)',
                    self._id)
                # If TCP closed before WebSocket, then record it as an abnormal
                # closure.
                if not self._wsproto.closed:
                    self._abort_web_socket()
                break
            else:
                logger.debug('conn#%d received %d bytes', self._id, len(data))
                self._wsproto.receive_bytes(data)

        logger.debug('conn#%d reader task finished', self._id)

    async def _write_pending(self):
        ''' Write any pending protocol data to the network socket. '''
        data = self._wsproto.bytes_to_send()
        if len(data) > 0:
            # The reader task and one or more writers might try to send messages
            # at the same time, so we need to synchronize access to this stream.
            async with self._stream_lock:
                logger.debug('conn#%d sending %d bytes', self._id, len(data))
                try:
                    await self._stream.send_all(data)
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    self._abort_web_socket()
                    raise ConnectionClosed(self._close_reason) from None
        else:
            logger.debug('conn#%d no pending data to send', self._id)


@attr.s
class ListenPort:
    ''' Represents a listener on a given address and port. '''
    address = attr.ib(converter=ip_address)
    port = attr.ib()
    is_ssl = attr.ib()

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

    def __init__(self, handler, listeners, *, handler_nursery=None):
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
        '''
        if len(listeners) == 0:
            raise ValueError('Listeners must contain at least one item.')
        self._handler = handler
        self._handler_nursery = handler_nursery
        self._listeners = listeners

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
            connection = WebSocketConnection(stream, wsproto)
            nursery.start_soon(connection._reader_task)
            async with connection:
                await connection._open_handshake.wait()
                await self._handler(connection)
            nursery.cancel_scope.cancel()
