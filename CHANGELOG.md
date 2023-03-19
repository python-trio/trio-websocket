# Release history

## trio-websocket 0.10.2 (2023-03-19)
### Fixed
- fixed a race condition where, just after a local-initiated close, the
  `closed` attribute would be `None`, and `send_message()` would be silently
  ignored (wsproto < 0.2.0) or leak a `LocalProtocolError` (wsproto >= 0.2.0)
  rather than raise `ConnectionClosed`
  ([#158](https://github.com/HyperionGray/trio-websocket/issues/158))

## trio-websocket 0.10.1 (2023-03-18)
### Fixed
- `send_message()` is changed to raise `ConnectionClosed` when a close
  handshake is in progress.  Previously, it would silently ignore
  the call, which was an oversight, given that `ConnectionClosed` is
  defined to cover connections "closed or in the process of closing".
  Notably, this fixes `send_message()` leaking a wsproto `LocalProtocolError`
  with wsproto >= 1.2.0.
  ([#175](https://github.com/HyperionGray/trio-websocket/issues/175))

Released as a minor version increment, since code calling `send_message()`
is expected to handle `ConnectionClosed` anyway.

## trio-websocket 0.10.0 (2023-03-13)
### Fixed
- avoid MultiError warnings with trio >= 0.22
### Changed
- drop support for Python 3.5, 3.6

## trio-websocket 0.9.2 (2021-02-05)
### Fixed
- the server will now correctly close the TCP stream on a CloseConnection event
  ([#115](https://github.com/HyperionGray/trio-websocket/issues/115))

## trio-websocket 0.9.1 (2020-12-06)
### Fixed
- fix client open_websocket_url() when the URL path component is empty
  ([#148](https://github.com/HyperionGray/trio-websocket/issues/148))

## trio-websocket 0.9.0 (2020-11-25)

> **_NOTE:_** `wsaccel`, which was important for good performance of
>`wsproto <= 0.14`, has been dropped as a trio-websocket requirement.  So
> ensure that your app either upgrades to `wsproto >= 0.15` or explicitly
> requires `wsaccel`.

### Changed
- allow dependency on recent `wsproto` versions
- eliminate `yarl`, `ipaddress`, and `wsaccel` dependencies
### Fixed
- avoid contributing to dropped exceptions during finalization.
  (See Trio issue https://github.com/python-trio/trio/issues/1559 for background.)

## trio-websocket 0.8.1 (2020-09-22)
### Fixed
- reader task no longer raises unhandled exception on ClosedResourceError
  ([#134](https://github.com/HyperionGray/trio-websocket/issues/134))
- minor issues in example code, documentation, and type-hinting

## ...
