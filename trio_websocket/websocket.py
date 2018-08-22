import itertools
import logging
import readline # enables history for input()

import trio
import wsproto.connection as wsconnection
import wsproto.events as wsevents
import wsproto.frame_protocol as wsframeproto


RECEIVE_BYTES = 4096
logger = logging.getLogger('trio-websocket')


class WebSocketConnectionClosed(Exception):
    ''' Indicates that a connection has closed. '''
    def __init__(self, code, name, reason):
        '''
        Constructor.

        :param int code: integer code
        :param str name: name of code
        :param str reason: reason provided by peer
        '''
        self.code = code
        self.name = name
        self.reason = reason

    def __repr__(self):
        ''' Return representation. '''
        return '<{} code={} name={} reason={}>'.format(self.__class__.__name__,
            self.code, self.name, self.reason)


class WebSocketConnection:
    ''' A WebSocket connection. '''

    CONNECTION_ID = itertools.count()

    def __init__(self, stream, wsproto):
        '''
        Constructor.

        :param SocketStream stream:
        :param bool client: true if this is a client, false if it is a server
        :param client: a Trio cancel scope
        '''
        self._closed_code = None
        self._closed_reason = None
        self._id = next(self.__class__.CONNECTION_ID)
        self._message_queue = trio.Queue(0)
        self._stream = stream
        self._wsproto = wsproto
        self._bytes_message = b''
        self._str_message = ''

    @property
    def closed_code(self):
        ''' The numeric close code, or None if not closed. '''
        return self._closed_code.value

    @property
    def closed_reason(self):
        ''' The close reason, or None if not closed. '''
        return self._closed_reason

    @property
    def is_client(self):
        ''' Is this a client instance? '''
        return self._wsproto.client

    @property
    def is_server(self):
        ''' Is this a server instance? '''
        return not self._wsproto.client

    async def close(self, code=None, reason=None):
        '''
        Close the WebSocket connection.

        This sends a closing frame and suspends until the connection is closed.
        Raises ``WebSocketConnectionClosed`` if the connection is already
        closed.

        :param int code: integer code
        :param str reason: reason to provide to peer
        '''
        self._wsproto.close(code=code or 1000, reason=reason)
        await self._write_pending()

    async def get_message(self):
        '''
        Return the next WebSocket message.

        Suspends until a message is available. Raises
        ``WebSocketConnectionClosed`` if the connection is closed.

        :return: str or bytes
        '''
        next_ = await self._message_queue.get()
        if isinstance(next_, Exception):
            raise next_
        else:
            return next_

    async def ping(self, payload):
        '''
        Send WebSocket ping to peer.

        Does not wait for pong reply. (This may change in the future._ Raises
        ``WebSocketConnectionClosed`` if the connection is closed.

        :param payload: str or bytes payloads
        '''
        self._wsproto.ping(payload)
        await self._write_pending()

    async def send_message(self, message):
        '''
        Send a WebSocket message.

        This method is currently async even though it never suspends because...
        reasons. It just seems like sending data should be async. Maybe wsproto
        will change some day in the future.

        Raises ``WebSocketConnectionClosed`` if the connection is closed.

        :param message: str or bytes
        '''
        self._wsproto.send_data(message)
        await self._write_pending()

    async def _close_message_queue(self):
        '''
        If any callers are suspended on get_message(), wake up each one
        with a ``WebSocketClosedException``.
        '''
        exc = WebSocketConnectionClosed(self._closed_code.value,
            self._closed_code.name, self._closed_reason)
        logger.debug('conn#%d websocket closed %r', self._id, exc)
        while True:
            try:
                self._message_queue.put_nowait(exc)
                await trio.sleep(0)
            except trio.WouldBlock:
                break

    async def _handle_event(self, event):
        '''
        Process one WebSocket event.

        :param event: a wsproto event
        '''
        if isinstance(event, wsevents.ConnectionRequested):
            logger.debug('conn#%d accepting websocket', self._id)
            self._wsproto.accept(event)
            await self._write_pending()
        elif isinstance(event, wsevents.ConnectionEstablished):
            logger.debug('conn#%d websocket established', self._id)
        elif isinstance(event, wsevents.ConnectionClosed):
            # If we are replying to a close frame, then wsproto puts a reply
            # in out outgoing buffer automatically. If we intitiated the close,
            # then this is a no-op:
            await self._write_pending()
            await self._set_closed(event.code, event.reason)
        elif isinstance(event, wsevents.BytesReceived):
            logger.debug('conn#%d received binary frame', self._id)
            self._bytes_message += event.data
            if event.message_finished:
                await self._message_queue.put(self._bytes_message)
                self._bytes_message = b''
        elif isinstance(event, wsevents.TextReceived):
            logger.debug('conn#%d received text frame', self._id)
            self._str_message += event.data
            if event.message_finished:
                await self._message_queue.put(self._str_message)
                self._str_message = ''
        elif isinstance(event, wsevents.PingReceived):
            logger.debug('conn#%d ping', self._id)
            # wsproto queues a pong automatically, we just need to send it:
            await self._write_pending()
        elif isinstance(event, wsevents.PongReceived):
            logger.debug('conn#%d pong %r', self._id, event.payload)
        else:
            raise Exception('Unknown websocket event: {!r}'.format(event))

    async def _reader_task(self):
        # Clients need to initate the negotiation:
        if self._wsproto.client:
            await self._write_pending()

        while True:
            # Get network data.
            data = await self._stream.receive_some(RECEIVE_BYTES)
            if len(data) == 0:
                logger.debug('conn#%d received zero bytes (connection closed)',
                    self._id)
                if self._wsproto.closed:
                    # WebSocket is already closed, so this is an expected
                    # closure.
                    if self.is_client:
                        await self._stream.send_eof()
                    await self._stream.aclose()
                else:
                    # This is an unexpected closure.
                    await self._set_closed(
                        wsframeproto.CloseReason.ABNORMAL_CLOSURE,
                        'TCP connection dropped unexpectedly')
                    await self._stream.aclose()
                break
            else:
                logger.debug('conn#%d received %d bytes', self._id, len(data))
                self._wsproto.receive_bytes(data)

            # Process new events.
            for event in self._wsproto.events():
                await self._handle_event(event)

        logger.debug('conn#%d reader task finished', self._id)

    async def _set_closed(self, code, reason):
        '''
        Set internal state to closed.

        :param code: a wsproto CloseReason
        :param str reason:
        '''
        self._closed_code = code
        self._closed_reason = reason
        await self._close_message_queue()

        # The server should initiate TCP teardown:
        if self.is_server:
            await self._stream.send_eof()

    async def _write_pending(self):
        ''' Write any pending protocol data to the network socket. '''
        data = self._wsproto.bytes_to_send()
        if len(data) > 0:
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

    def __init__(self, handler, ip, port):
        '''
        Constructor.

        :param coroutine handler: the coroutine to call to handle a new
            connection
        :param str ip: the IP address to bind to
        :param int port: the port to bind to
        '''
        self._handler = handler
        self._ip = ip or None
        self._port = port

    async def listen(self):
        ''' Listen for incoming connections. '''
        try:
            logger.info('Listening on %s:%d', self._ip, self._port)
            await trio.serve_tcp(self._handle_connection, self._port,
                host=self._ip)
        except KeyboardInterrupt:
            logger.info('Received SIGINT... shutting down')

    async def _handle_connection(self, stream):
        ''' Handle an incoming connection. '''
        async with trio.open_nursery() as nursery:
            wsproto = wsconnection.WSConnection(wsconnection.SERVER)
            connection = WebSocketConnection(stream, wsproto)
            nursery.start_soon(connection._reader_task)
            nursery.start_soon(self._handler, connection)


class WebSocketClient:
    ''' WebSocket client. '''

    def __init__(self, host, port, resource):
        '''
        Constructor.

        :param str host: the host to connect to
        :param int port: the port to connect to
        :param str resource: the resource (i.e. path without leading slash)
        '''
        self._host = host
        self._port = port
        self._resource = resource

    async def connect(self, nursery):
        '''
        Connect to WebSocket server.
        '''
        stream = await trio.open_tcp_stream(self._host, self._port)
        wsproto = wsconnection.WSConnection(wsconnection.CLIENT,
            host=self._host, resource=self._resource)
        connection = WebSocketConnection(stream, wsproto)
        nursery.start_soon(connection._reader_task)
        return connection
