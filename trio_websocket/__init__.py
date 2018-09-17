import enum
import itertools
import logging
import ssl
from functools import partial

from async_generator import async_generator, yield_, asynccontextmanager
import trio
import trio.abc
import wsproto.connection as wsconnection
import wsproto.events as wsevents
import wsproto.frame_protocol as wsframeproto
from yarl import URL


__version__ = '0.2.0-dev'
RECEIVE_BYTES = 4096
logger = logging.getLogger('trio-websocket')


@asynccontextmanager
@async_generator
async def open_websocket(nursery, host, port, resource, use_ssl):
    '''
    Open a WebSocket client connection to a host.

    This function is an async context manager that automatically connects and
    disconnects. It yields a `WebSocketConnection` instance.

    :param nursery: a trio Nursery to run background tasks in
    :param str host: the host to connect to
    :param int port: the port to connect to
    :param str resource: the resource (i.e. path without leading slash)
    :param use_ssl: a bool or SSLContext
    '''

    if use_ssl == True:
        ssl_context = ssl.create_default_context()
    elif use_ssl == False:
        ssl_context = None
    elif isinstance(use_ssl, ssl.SSLContext):
        ssl_context = use_ssl
    else:
        raise TypeError('`use_ssl` argument must be bool or ssl.SSLContext')

    logger.debug('Connecting to ws%s://%s:%d/%s',
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
    wsproto = wsconnection.WSConnection(wsconnection.CLIENT, host=host_header,
        resource=resource)
    connection = WebSocketConnection(stream, wsproto, path=resource)
    nursery.start_soon(connection._reader_task)
    await connection._open_handshake.wait()
    async with connection:
        await yield_(connection)


@asynccontextmanager
@async_generator
async def open_websocket_url(nursery, url, ssl_context=None):
    '''
    Open a WebSocket client connection to a URL.

    This function is an async context manager that automatically connects and
    disconnects. It yields a `WebSocketConnection` instance.

    :param str url: a WebSocket URL
    :param ssl_context: optional SSLContext, only used for wss: URL scheme
    '''
    url = URL(url)
    if url.scheme not in ('ws', 'wss'):
        raise ValueError('WebSocket URL scheme must be "ws:" or "wss:"')
    resource = url.path.lstrip('/')
    if ssl_context is None:
        use_ssl = url.scheme == 'wss'
    else:
        use_ssl = ssl_context
    async with open_websocket(nursery, url.host, url.port, resource, use_ssl) \
        as conn:
        await yield_(conn)


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
        self._message_queue = trio.Queue(0)
        self._stream = stream
        self._stream_lock = trio.StrictFIFOLock()
        self._wsproto = wsproto
        self._bytes_message = b''
        self._str_message = ''
        self._reader_running = True
        self._path = path
        # Set once the WebSocket open handshake takes place, i.e.
        # ConnectionRequested for server or ConnectedEstablished for client.
        self._open_handshake = trio.Event()
        # Set once a WebSocket closed handshake takes place, i.e after a close
        # frame has been sent and a close frame has been received.
        self._close_handshake = trio.Event()

    @property
    def closed(self):
        '''
        If the WebSocket connection is open and usable, this property is None.
        If the WebSocket connection is closed, no further operations are
        permitted and this property contains a ``CloseReason`` object indicating
        why the connection was closed.
        '''
        return self._close_reason

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
        After calling this method, any futher operations on this WebSocket (such
        as ``get_message()`` or ``send_message()``) will raise
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
        next_ = await self._message_queue.get()
        if isinstance(next_, Exception):
            raise next_
        else:
            return next_

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

    async def _close_stream(self):
        ''' Close the TCP connection. '''
        self._reader_running = False
        try:
            await self._stream.aclose()
        except trio.BrokenStreamError:
            # This means the TCP connection is already dead.
            pass

    async def _close_web_socket(self, code, reason):
        '''
        Mark the WebSocket as closed. If any tasks are suspended on
        get_message(), wake them up with a ConnectionClosed exception.
        '''
        self._close_reason = CloseReason(code, reason)
        exc = ConnectionClosed(self._close_reason)
        logger.debug('conn#%d websocket closed %r', self._id, exc)
        while True:
            try:
                self._message_queue.put_nowait(exc)
                await trio.sleep(0)
            except trio.WouldBlock:
                break

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
        await self._close_web_socket(event.code, event.reason or None)
        self._close_handshake.set()

    async def _handle_bytes_received_event(self, event):
        '''
        Handle a BytesReceived event.

        :param event:
        '''
        self._bytes_message += event.data
        if event.message_finished:
            await self._message_queue.put(self._bytes_message)
            self._bytes_message = b''

    async def _handle_text_received_event(self, event):
        '''
        Handle a TextReceived event.

        :param event:
        '''
        self._str_message += event.data
        if event.message_finished:
            await self._message_queue.put(self._str_message)
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
            except trio.ClosedResourceError:
                break
            if len(data) == 0:
                logger.debug('conn#%d received zero bytes (connection closed)',
                    self._id)
                # If TCP closed before WebSocket, then record it as an abnormal
                # closure.
                if not self._wsproto.closed:
                    await self._close_web_socket(
                        wsframeproto.CloseReason.ABNORMAL_CLOSURE,
                        'TCP connection aborted')
                await self._close_stream()
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
                await self._stream.send_all(data)
        else:
            logger.debug('conn#%d no pending data to send', self._id)


class WebSocketServer:
    '''
    WebSocket server.

    The server class listens on a TCP socket. For each incoming connection,
    it creates a ``WebSocketConnection`` instance, starts some background tasks
    (in a new nursery),
    '''

    def __init__(self, handler, ip, port, ssl_context):
        '''
        Constructor.

        :param coroutine handler: the coroutine called with the corresponding
            WebSocketConnection on each new connection.  The call will be made
            once the HTTP handshake completes, which notably implies that the
            connection's `path` property will be valid.
        :param str ip: the IP address to bind to
        :param int port: the port to bind to
        :param ssl_context: an SSLContext or None for plaintext
        '''
        self._handler = handler
        self._ip = ip or None
        self._port = port
        self._ssl = ssl_context

    @property
    def port(self):
        """Returns the requested or kernel-assigned port number.

        In the case of kernel-assigned port (requested with port=0 in the
        constructor), the assigned port will be reflected after calling
        starting the `listen` task.  (Technically, once listen reaches the
        "started" state.)
        """
        return self._port

    async def listen(self, *, task_status=trio.TASK_STATUS_IGNORED):
        ''' Listen for incoming connections. '''
        if self._ssl is None:
            serve = partial(trio.serve_tcp, self._handle_connection,
                self._port, host=self._ip)
        else:
            serve = partial(trio.serve_ssl_over_tcp, self._handle_connection,
                self._port, ssl_context=self._ssl, https_compatible=True,
                host=self._ip)
        async with trio.open_nursery() as nursery:
            listener = (await nursery.start(serve))[0]
            self._port = listener.socket.getsockname()[1]
            logger.debug('Listening on http%s://%s:%d',
                '' if self._ssl is None else 's', self._ip, self._port)
            task_status.started()
            await trio.sleep_forever()

    async def _handle_connection(self, stream):
        ''' Handle an incoming connection. '''
        async with trio.open_nursery() as nursery:
            wsproto = wsconnection.WSConnection(wsconnection.SERVER)
            connection = WebSocketConnection(stream, wsproto)
            nursery.start_soon(connection._reader_task)
            await connection._open_handshake.wait()
            nursery.start_soon(self._handler, connection)


