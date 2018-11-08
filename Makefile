# This .PHONY line prevents make from treating the docs/ directory like a build
# product:
.PHONY: docs

export PYTHONPATH = .

coverage:
	coveralls -v

docs:
	$(MAKE) -C docs html

test:
	pytest --cov=trio_websocket

publish:
	python setup.py sdist
	twine upload dist/*
	rm -fr build dist .egg trio_websocket.egg-info
