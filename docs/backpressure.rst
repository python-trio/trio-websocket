Message Queues
==============

.. currentmodule:: trio_websocket

.. TODO This file will grow into a "backpressure" document once #65 is complete.
    For now it is just deals with userspace buffers, since this is a related
    topic.

When a connection is open, it runs a background task that reads network data and
automatically handles certain types of events for you. For example, if the
background task receives a ping event, then it will automatically send back a
pong event. When the background task receives a message, it places that message
into an internal queue. When you call ``get_message()``, it returns the first
item from this queue.

If this internal message queue does not have any size limits, then a remote
endpoint could rapidly send large messages and use up all of the memory on the
local machine! In almost all situations, the message queue needs to have size
limits, both in terms of the number of items and the size per message. These
limits create an upper bound for the amount of memory that can be used by a
single WebSocket connection. For example, if the queue size is 10 and the
maximum message size is 1 megabyte, then the connection will use at most 10
megabytes of memory.

When the message queue is full, the background task pauses and waits for the
user to remove a message, i.e. call ``get_message()``. When the background task
is paused, it stops processing background events like replying to ping events.
If a message is received that is larger than the maximum message size, then the
connection is automatically closed with code 1009 and the message is discarded.

The library APIs each take arguments to configure the mesage buffer:
``message_queue_size`` and ``max_message_size``. By default the queue size is
one and the maximum message size is 1 MiB. If you set queue size to zero, then
the background task will block every time it receives a message until somebody
calls ``get_message()``. For an unbounded queue—which is strongly
discouraged—set the queue size to ``math.inf``. Likewise, the maximum message
size may also be disabled by setting it to ``math.inf``.
