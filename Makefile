PYTHON = python

# This .PHONY line prevents make from treating the docs/ directory like a build
# product:
.PHONY: docs

coverage:
	$(PYTHON) -m coveralls -v

docs:
	$(MAKE) -C docs html

test:
	$(PYTHON) -m pytest --cov=trio_websocket --no-cov-on-fail

lint:
	$(PYTHON) -m pylint trio_websocket/ tests/ autobahn/ examples/
	! $(PYTHON) --version | grep -q 'PyPy' && $(PYTHON) -m mypy trio_websocket/

publish:
	rm -fr build dist .egg trio_websocket.egg-info
	$(PYTHON) setup.py sdist
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
requirements-dev.txt: setup.py requirements-dev.in
	pip-compile -q $(PIP_COMPILE_ARGS) --output-file $@ $^
endif
