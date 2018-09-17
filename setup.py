from setuptools import setup, find_packages
from pathlib import Path
from trio_websocket import __version__ as version


here = Path(__file__).parent

# Get description
with open(here / 'README.md', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='trio-websocket',
    version=version,
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
    ],
    keywords='websocket client server trio',
    packages=find_packages(exclude=['docs', 'examples', 'tests']),
    install_requires=['async_generator', 'trio', 'wsaccel', 'wsproto', 'yarl'],
    extras_require={
        'dev': ['pytest', 'pytest-trio', 'trustme'],
    },
    project_urls={
        'Bug Reports': 'https://github.com/HyperionGray/trio-websocket/issues',
        'Source': 'https://github.com/HyperionGray/trio-websocket',
    },
)
