PYTHON = python

# This .PHONY line prevents make from treating the docs/ directory like a build
# product:
.PHONY: docs

docs:
	$(MAKE) -C docs html

test:
	$(PYTHON) -m pytest --cov=trio_websocket --cov-report=term-missing --no-cov-on-fail

lint:
	$(PYTHON) -m pylint trio_websocket/ tests/ autobahn/ examples/

typecheck:
	$(PYTHON) -m mypy --explicit-package-bases trio_websocket tests autobahn examples

publish:
	rm -fr build dist .egg trio_websocket.egg-info
	! grep -q dev trio_websocket/_version.py
	$(PYTHON) -m build
	twine check dist/*
	twine upload dist/*

# requirements-dev.txt will only be regenerated when PIP_COMPILE_ARGS is not
# empty, and requires installatation of pip-tools.
#
# To change requirements, edit setup.py and requirements-dev.in files as necessary, then:
#   make -W requirements-dev.{in,txt} PIP_COMPILE_ARGS="-q"
# upgrade all deps:
#   make -W requirements-dev.{in,txt} PIP_COMPILE_ARGS="-U"
# upgrade specific deps:
#   make -W requirements-dev.{in,txt} PIP_COMPILE_ARGS="-P foo"
ifneq ($(PIP_COMPILE_ARGS),)
requirements-dev-full.txt: setup.py requirements-dev.in requirements-extras.in
	pip-compile -q $(PIP_COMPILE_ARGS) --output-file $@ $^

requirements-dev.txt: setup.py requirements-dev.in
	pip-compile -q $(PIP_COMPILE_ARGS) --output-file $@ $^
endif
