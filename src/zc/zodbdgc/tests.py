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
from zope.testing import setupstack, renormalizing
import binascii
import doctest
import mock
import re
import unittest
import zc.zodbdgc
import ZODB.config


def untransform(data):
    if data[:2] == b'.h':
        data = binascii.a2b_hex(data[2:])
    return data


def hex_pack(self, pack_time, referencesf, *args):
    def refs(p, oids=None):
        if p and p[:2] == b'.h':
            p = binascii.a2b_hex(p[2:])
        return referencesf(p, oids)
    return self.base.pack(pack_time, refs, *args)


def test_untransform():
    r"""
If a file storage is transformed, you can use the --untransform/-u
option with the --file-storage/-f option to specify a function to
untransform data records when accessing the file-storage file directly.

.. XXX whimper hexstorage's pack is broken.

    >>> import ZODB.tests.hexstorage
    >>> ZODB.tests.hexstorage.HexStorage.pack = hex_pack

First, open a database and create some data:

    >>> f = open('config', 'w')
    >>> _ = f.write('''
    ... %import ZODB.tests
    ... <zodb>
    ...   <hexstorage>
    ...     <filestorage>
    ...       path data.fs
    ...       pack-gc false
    ...     </filestorage>
    ...   </hexstorage>
    ... </zodb>
    ... ''')
    >>> f.close()

    >>> with open('config', 'r') as f:
    ...     db = ZODB.config.databaseFromFile(f)
    >>> conn = db.open()
    >>> for i in range(9):
    ...     conn.root()[i] = conn.root().__class__()
    ...     conn.transaction_manager.commit()

Now, we'll try to make some garbage:

    >>> for i in range(3):
    ...     del conn.root()[i]
    ...     conn.transaction_manager.commit()

The records we just deleted aren't garbage yet, because there are
revisions pointing to them.

    >>> db.pack()

Now there aren't. :)

    >>> for i in range(3, 6):
    ...     del conn.root()[i]
    ...     conn.transaction_manager.commit()

Save the current tid so we can give it to gc:

    >>> ptid = conn.root()._p_serial

Delete some more:

    >>> for i in range(6, 9):
    ...     del conn.root()[i]
    ...     conn.transaction_manager.commit()

    >>> len(db.storage)
    10
    >>> db.close()

Now GC. We should lose 3 objects:

    >>> zc.zodbdgc.gc_command(
    ...   '-f=data.fs -uzc.zodbdgc.tests:untransform config'
    ...   .split(), ptid, return_bad=True)
    [('', 1), ('', 2), ('', 3)]

    >>> with open('config', 'r') as f:
    ...     db = ZODB.config.databaseFromFile(f)
    >>> db.pack()
    >>> len(db.storage)
    7
    >>> db.close()
    """


def stupid_typo_nameerror_not():
    """

    >>> f = open('config', 'w')
    >>> _ = f.write('''
    ... <zodb db>
    ...     <filestorage>
    ...         pack-gc false
    ...         pack-keep-old false
    ...         path 1.fs
    ...     </filestorage>
    ... </zodb>
    ... ''')
    >>> f.close()
    >>> import persistent.mapping, time
    >>> with mock.patch("time.time", return_value=1241458549.614022):
    ...     with open('config') as f:
    ...          db = ZODB.config.databaseFromFile(f)
    ...     conn = db.open()
    ...     junk = persistent.mapping.PersistentMapping()
    ...     conn.add(junk)
    ...     conn.transaction_manager.commit()
    ...     junk['a'] = 1
    ...     conn.transaction_manager.commit()
    ...     junk['a'] = 2
    ...     conn.transaction_manager.commit()
    ...     junk['a'] = 3
    ...     conn.transaction_manager.commit()
    ...     time.time.return_value += 86400*1.5
    ...     conn.root.x = 1
    ...     conn.transaction_manager.commit()
    ...     db.close()
    ...     import zc.zodbdgc, time
    ...     bad = zc.zodbdgc.gc('config', days=1, return_bad=True)
    ...     for name, oid in bad:
    ...         print( "{0} {1}".format(name, oid) )
    ...     time.time.return_value += 86400*1.5
    ...     with open('config', 'r') as f:
    ...         db = ZODB.config.databaseFromFile(f)
    ...     len(db.storage)
    ...     db.pack()
    ...     len(db.storage)
    db 1
    2
    1
    >>> db.close()
    """


def test_missmatched_configs():
    """
    >>> f = open('config1', 'w')
    >>> _ = f.write('''
    ... <zodb>
    ...     <filestorage>
    ...         pack-gc false
    ...         pack-keep-old false
    ...         path 1.fs
    ...     </filestorage>
    ... </zodb>
    ... ''')
    >>> f.close()
    >>> f = open('config2', 'w')
    >>> _ = f.write('''
    ... <zodb db>
    ...     <filestorage>
    ...         pack-gc false
    ...         pack-keep-old false
    ...         path 2.fs
    ...     </filestorage>
    ... </zodb>
    ... ''')
    >>> f.close()
    >>> bad = zc.zodbdgc.gc('config1', conf2='config2')
    Traceback (most recent call last):
    ...
    ValueError: primary and secondary databases don't match.

    """


def test_suite():
    suite = unittest.TestSuite((
        doctest.DocFileSuite(
            'README.test', 'oidset.test',
            setUp=setupstack.setUpDirectory, tearDown=setupstack.tearDown,
            checker=renormalizing.RENormalizing([
                (re.compile('usage'), 'Usage'),
                (re.compile('options'), 'Options'),
            ]),
        ),
    ))
    try:
        import ZODB.tests.hexstorage  # noqa: F401 import unused
    except ImportError:
        pass
    else:
        suite.addTest(doctest.DocTestSuite(
            setUp=setupstack.setUpDirectory, tearDown=setupstack.tearDown,
        ))
    return suite
