PYTHON = python

# This .PHONY line prevents make from treating the docs/ directory like a build
# product:
.PHONY: docs

coverage:
	$(PYTHON) -m coveralls -v

docs:
	$(MAKE) -C docs html

test:
	$(PYTHON) -m pytest --cov=trio_websocket

publish:
	rm -fr build dist .egg trio_websocket.egg-info
	$(PYTHON) setup.py sdist
	twine upload dist/*

# upgrade all deps:
#   make -W requirements-dev.{in,txt} PIP_COMPILE_ARGS="-U"
# upgrade specific deps:
#   make -W requirements-dev.{in,txt} PIP_COMPILE_ARGS="-P foo"
requirements-dev.txt: setup.py requirements-dev.in
	pip-compile -q $(PIP_COMPILE_ARGS) --output-file $@ $^
