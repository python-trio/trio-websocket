cat make_release.sh 
#!/bin/bash

rm -fr dist
python3 setup.py sdist
twine upload dist/*
