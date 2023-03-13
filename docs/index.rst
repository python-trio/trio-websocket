Trio WebSocket
==============

This library is a WebSocket implementation for `the Trio framework
<https://trio.readthedocs.io/en/latest/>`__ that strives for safety,
correctness, and ergonomics. It is based on `wsproto
<https://wsproto.readthedocs.io/en/latest/>`__, which is a `Sans-IO
<https://sans-io.readthedocs.io/>`__ state machine that implements most aspects
of the WebSocket protocol, including framing, codecs, and events. The
respository is hosted `on GitHub
<https://github.com/hyperiongray/trio-websocket/>`__. This library passes `the
Autobahn Test Suite <https://github.com/crossbario/autobahn-testsuite>`__.

.. image:: https://img.shields.io/pypi/v/trio-websocket.svg?style=flat-square
    :alt: PyPI
    :target: https://pypi.org/project/trio-websocket/
.. image:: https://img.shields.io/pypi/pyversions/trio-websocket.svg?style=flat-square
    :alt: Python Versions
.. image:: https://img.shields.io/github/license/HyperionGray/trio-websocket.svg?style=flat-square
    :alt: MIT License
.. image:: https://img.shields.io/github/actions/workflow/status/HyperionGray/trio-websocket/ci.yml
    :alt: Build Status
    :target: https://github.com/HyperionGray/trio-websocket/actions/workflows/ci.yml

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   getting_started
   clients
   servers
   backpressure
   timeouts
   api
   recipes
   contributing
   credits
