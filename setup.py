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

name, version = 'zc.zodbdgc', '0'

import os
from setuptools import setup, find_packages

entry_points = """
[console_scripts]
multi-zodb-gc = zc.zodbdgc:gc_command
multi-zodb-check-refs = zc.zodbdgc:check_command
"""

def read(rname):
    return open(os.path.join(os.path.dirname(__file__), *rname.split('/')
                             )).read()

long_description = (
        read('src/%s/README.txt' % '/'.join(name.split('.')))
        + '\n' +
        'Download\n'
        '--------\n'
        )

tests_require = ['zope.testing', 'mock']

setup(
    name = name,
    version = version,
    author = 'Jim Fulton',
    author_email = 'jim@zope.com',
    description = 'ZODB Distributed Garbage Collection',
    long_description=long_description,
    license = 'ZPL 2.1',

    packages = find_packages('src'),
    namespace_packages = ['zc'],
    package_dir = {'': 'src'},
    install_requires = ['setuptools', 'ZODB3 >=3.9.0b2'],
    zip_safe = False,
    entry_points=entry_points,
    include_package_data = True,
    tests_require=tests_require,
    extras_require=dict(
        test=tests_require,
        ),
    test_suite='zc.zodbdgc.tests.test_suite',
    )
