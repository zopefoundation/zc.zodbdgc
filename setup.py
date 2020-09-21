##############################################################################
#
# Copyright (c) Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################

import os
from setuptools import setup

name = 'zc.zodbdgc'
version = '1.1.0'


entry_points = """
[console_scripts]
multi-zodb-gc = zc.zodbdgc:gc_command
multi-zodb-check-refs = zc.zodbdgc:check_command
"""

def read(rname):
    with open(os.path.join(os.path.dirname(__file__), *rname.split('/'))) as f:
        return f.read()

long_description = (
    read('src/%s/README.txt' % '/'.join(name.split('.')))
    + '\n' +
    read('CHANGES.rst')
    + '\n' +
    'Download\n'
    '========\n'
)

tests_require = [
    'zope.testing',
    'mock >= 1.3.0',
    'zope.testrunner',
]
install_requires = [
    "BTrees >= 4.0.0",
    "ZODB >= 5.1.0", # TransactionMetaData added in 5.1
    "persistent >= 4.0.0",
    "setuptools >= 17.1",
    "transaction",
    # PyPy, Jython, and Py3K don't have cPickle's `noload`, and `noload`
    # is broken in CPython >= 2.7. Use zodbpickle everywhere, even on
    # cPython 2.6, for consistency and to avoid issues with wheels and
    # dynamic install_requires.
    "zodbpickle",
]


setup(
    name=name,
    version=version,
    author='Jim Fulton',
    author_email='jim@zope.com',
    description='ZODB Distributed Garbage Collection',
    long_description=long_description,
    license='ZPL 2.1',
    url="https://github.com/zopefoundation/zc.zodbdgc",
    keywords="database nosql python zope zodb garbage collection distributed",

    packages=['zc', 'zc.zodbdgc'],
    namespace_packages=['zc'],
    package_dir={'': 'src'},
    install_requires=install_requires,
    zip_safe=False,
    entry_points=entry_points,
    include_package_data=True,
    tests_require=tests_require,
    extras_require=dict(
        test=tests_require,
    ),
    test_suite='zc.zodbdgc.tests.test_suite',
    classifiers=[
        "Framework :: ZODB",
        "License :: OSI Approved :: Zope Public License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
    ],
)
