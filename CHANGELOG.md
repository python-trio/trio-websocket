# Release history

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
