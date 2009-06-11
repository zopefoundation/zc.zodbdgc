##############################################################################
#
# Copyright (c) 2004 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.0 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""XXX short summary goes here.

$Id$
"""
from zope.testing import doctest, setupstack, renormalizing
import re
import unittest

def test_suite():
    return unittest.TestSuite((
        doctest.DocFileSuite(
            'README.test', 'oidset.test',
            setUp=setupstack.setUpDirectory, tearDown = setupstack.tearDown,
            checker=renormalizing.RENormalizing([
                (re.compile('usage'), 'Usage'),
                (re.compile('options'), 'Options'),
                ]),
            ),
        ))

