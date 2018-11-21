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
