from setuptools import setup, find_packages
from pathlib import Path

here = Path(__file__).parent

# Get version
version = {}
with (here / "trio_websocket" / "version.py").open() as f:
    exec(f.read(), version)


# Get description
with (here / 'README.md').open(encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='trio-websocket',
    version=version['__version__'],
    description='WebSocket library for Trio',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/HyperionGray/trio-websocket',
    author='Mark E. Haase',
    author_email='mehaase@gmail.com',
    classifiers=[
        # See https://pypi.org/classifiers/
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
    ],
    python_requires=">=3.5",
    keywords='websocket client server trio',
    packages=find_packages(exclude=['docs', 'examples', 'tests']),
    install_requires=[
        'async_generator>=1.10,<2',
        'ipaddress>=1.0.22,<2',
        'trio>=0.11',
        # TODO: confirm whether wsaccel is relevant to performance
        # Disabled on pypy: https://github.com/methane/wsaccel/issues/19
        'wsaccel>=0.6.2,<0.7;implementation_name!="pypy"',
        'wsproto>=0.14,<0.15',
        'yarl>=1.2.6,<2'
    ],
    project_urls={
        'Bug Reports': 'https://github.com/HyperionGray/trio-websocket/issues',
        'Source': 'https://github.com/HyperionGray/trio-websocket',
    },
)
