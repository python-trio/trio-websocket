Contributing
============

.. _developer-installation:

Developer Installation
----------------------

If you want to help contribute to ``trio-websocket``, then you will need to
install additional dependencies that are used for testing and documentation. The
following sequence of commands will clone the repository, create a virtual
environment, and install the developer dependencies::

    $ git clone git@github.com:HyperionGray/trio-websocket.git
    $ cd trio-websocket
    $ python3 -m venv venv
    $ source venv/bin/activate
    (venv) $ pip install -r requirements-dev-full.txt
    (venv) $ pip install -e .

This example uses Python's built-in ``venv`` package, but you can of course use
other virtual environment tools such as ``virtualenvwrapper``.

The ``requirements-dev.in`` and ``requirements-extras.in`` files contain extra
dependencies only needed for development, such as PyTest, Sphinx, etc.
The ``.in`` files and ``setup.py`` are used to generate the pinned manifests
``requirements-dev-full.txt`` and ``requirements-dev.txt``, so that dependencies
used in development and CI builds do not change arbitrarily over time.

Unit Tests
----------

.. note::

    This project has unit tests that are configured to run on all pull requests
    to automatically check for regressions. Each pull request should include
    unit test coverage before it is merged.

The unit tests are written with `the PyTest framework
<https://docs.pytest.org/en/latest/>`__. You can quickly run all unit tests from
the project's root with a simple command::

    (venv) $ pytest
    === test session starts ===
    platform linux -- Python 3.6.6, pytest-3.8.0, py-1.6.0, pluggy-0.7.1
    rootdir: /home/johndoe/code/trio-websocket, inifile: pytest.ini
    plugins: trio-0.5.0, cov-2.6.0
    collected 27 items

    tests/test_connection.py ........................... [100%]

    === 27 passed in 0.41 seconds ===

You can enable code coverage reporting by adding the ``-cov=trio_websocket``
option to PyTest or using the Makefile target ``make test``::

    (venv) $ pytest --cov=trio_websocket
    === test session starts ===
    platform linux -- Python 3.6.6, pytest-3.8.0, py-1.6.0, pluggy-0.7.1
    rootdir: /home/johndoe/code/trio-websocket, inifile: pytest.ini
    plugins: trio-0.5.0, cov-2.6.0
    collected 27 items

    tests/test_connection.py ........................... [100%]

    ---------- coverage: platform darwin, python 3.7.0-final-0 -----------
    Name                         Stmts   Miss  Cover
    ------------------------------------------------
    trio_websocket/__init__.py     369     33    91%
    trio_websocket/_version.py       1      0   100%
    ------------------------------------------------
    TOTAL                          370     33    91%


    === 27 passed in 0.57 seconds ===

Documentation
-------------

This documentation is stored in the repository in the ``/docs/`` directory. It
is written with `RestructuredText markup
<http://docutils.sourceforge.net/rst.html>`__ and processed by `Sphinx
<http://www.sphinx-doc.org/en/stable/>`__. To build documentation, run this
command from the project root::

    $ make docs

The finished documentation can be found in ``/docs/_build/``. This documentation
is published automatically to `Read The Docs <https://readthedocs.org/>`__ for
all pushes to master or to a tag.

Autobahn Client Tests
---------------------

The Autobahn Test Suite contains over 500 integration tests for WebSocket
servers and clients. These test suites are contained in a `Docker
<https://www.docker.com/>`__ container. You will need to install Docker before
you can run these integration tests.

To test the client, you will need two terminal windows. In the first terminal,
run the following commands::

    $ cd autobahn
    $ docker run -it --rm \
          -v "${PWD}/config:/config" \
          -v "${PWD}/reports:/reports" \
          -p 9001:9001 \
          --name autobahn \
          crossbario/autobahn-testsuite

The first time you run this command, Docker will download some files, which may
take a few minutes. When the test suite is ready, it will display::

    Autobahn WebSocket 0.8.0/0.10.9 Fuzzing Server (Port 9001)
    Ok, will run 249 test cases for any clients connecting

Now in the second terminal, run the Autobahn client::

    $ cd autobahn
    $ python client.py ws://localhost:9001
    INFO:client:Case count=249
    INFO:client:Running test case 1 of 249
    INFO:client:Running test case 2 of 249
    INFO:client:Running test case 3 of 249
    INFO:client:Running test case 4 of 249
    INFO:client:Running test case 5 of 249
    <snip>

When the client finishes running, an HTML report is published to the
``autobahn/reports/clients`` directory. If any tests fail, you can debug
individual tests by specifying the integer test case ID (not the dotted test
case ID), e.g. to run test case #29::

    $ python client.py ws://localhost:9001 29

Autobahn Server Tests
---------------------

Read the section on Autobahn client tests before you read this section. Once
again, you will need two terminal windows. In the first terminal, run::

    $ cd autobahn
    $ python server.py

In the second terminal, you will run the Docker image::

    $ cd autobahn
    $ docker run -it --rm \
          -v "${PWD}/config:/config" \
          -v "${PWD}/reports:/reports" \
          -p 9000:9000 \
          --name autobahn \
          crossbario/autobahn-testsuite \
          /usr/local/bin/wstest --mode fuzzingclient --spec /config/fuzzingclient.json

If a test fails, ``server.py`` does not support the same ``debug_cases``
argument as ``client.py``, but you can modify `fuzzingclient.json` to specify a
subset of cases to run, e.g. ``3.*`` to run all test cases in section 3.

.. note::

    For OS X or Windows, you'll need to edit `fuzzingclient.json` and
    change the host from ``172.17.0.1`` to ``host.docker.internal``.

Versioning
----------

This project `uses semantic versioning <https://semver.org/>`__ for official
releases. When a new version is released, the version number on the ``master``
branch will be incremented to the next expected release and suffixed "dev". For
example, if we release version 1.1.0, then the version number on ``master``
might be set to ``1.2.0-dev``, indicating that the next expected release is
``1.2.0`` and that release is still under development.

Release Process
---------------

To release a new version of this library, we follow this process:

1. In ``_version.py`` on ``master`` branch, remove the ``-dev`` suffix from the
   version number, e.g. change ``1.2.0-dev`` to ``1.2.0``.
2. Commit ``_version.py``.
3. Create a tag, e.g. ``git tag 1.2.0``.
4. Push the commit and the tag, e.g. ``git push && git push origin 1.2.0``.
5. Wait for `Github CI <https://github.com/HyperionGray/trio-websocket/actions/>`__ to
   finish building and ensure that the build is successful.
6. Wait for `Read The Docs <https://trio-websocket.readthedocs.io/en/latest/>`__
   to finish building and ensure that the build is successful.
7. Ensure that the working copy is in a clean state, e.g. ``git status`` shows
   no changes.
8. Build package and submit to PyPI: ``make publish``
9. In ``_version.py`` on ``master`` branch, increment the version number to the
   next expected release and add the ``-dev`` suffix, e.g. change ``1.2.0`` to
   ``1.3.0-dev``.
10. Commit and push ``_version.py``.
