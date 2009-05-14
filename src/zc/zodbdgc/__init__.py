##############################################################################
#
# Copyright (c) Zope Foundation and Contributors.
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

from ZODB.utils import u64, z64, p64
import BTrees.IIBTree
import base64
import cPickle
import cStringIO
import logging
import marshal
import optparse
import os
import shutil
import sys
import tempfile
import time
import transaction
import ZODB.config
import ZODB.TimeStamp

logger = logging.getLogger(__name__)

def gc(conf, days=1, conf2=None):
    db1 = ZODB.config.databaseFromFile(open(conf))
    if conf2 is None:
        db2 = db1
    else:
        logger.info("Using secondary configuration, %s, for analysis", conf2)
        db2 = ZODB.config.databaseFromFile(open(conf2))
        if set(db1.databases) != set(db2.databases):
            raise ValueError("primary and secondary databases don't match.")

    databases = db2.databases
    storages = dict((name, d.storage) for (name, d) in databases.items())

    ptid = repr(
        ZODB.TimeStamp.TimeStamp(*time.gmtime(time.time() - 86400*days)[:6])
        )

    # Pre-populate good with roots and recently-written objects
    good = oidset(databases)
    bad = oidset(databases)
    both = good, bad
    deleted = oidset(databases)
    baddir = tempfile.mkdtemp()
    for name in storages:
        os.mkdir(os.path.join(baddir, name))

    for name, storage in storages.iteritems():
        # Make sure we can get the roots
        _ = storage.load(z64, '')
        good.insert(name, z64)

        # All non-deleted new records are good
        for trans in storage.iterator(ptid):
            for record in trans:
                oid = record.oid
                data = record.data
                if data:
                    if deleted.has(name, oid):
                        raise AssertionError(
                            "Non-deleted record after deleted")
                    good.insert(name, oid)

                    # and anything they reference
                    for ref in getrefs(data, name):
                        if not deleted.has(*ref):
                            good.insert(*ref)
                else:
                    # deleted record
                    deleted.insert(name, oid)
                    if good.has(name, oid):
                        good.remove(name, oid)

        # Now iterate over older records
        for trans in storage.iterator(None, ptid):
            for record in trans:
                oid = record.oid
                data = record.data
                if data:
                    if deleted.has(name, oid):
                        continue
                    if good.has(name, oid):
                        for ref in getrefs(data, name):
                            if deleted.has(*ref):
                                continue
                            if good.insert(*ref) and bad.has(*ref):
                                bad_to_good(baddir, bad, good, *ref)
                    else:
                        bad.insert(name, oid)
                        refs = tuple(ref for ref in getrefs(data, name)
                                     if not (good.has(*ref) or
                                             deleted.has(*ref)
                                             )
                                     )
                        if not refs:
                            continue    # leaves are common
                        f = open(bad_path(baddir, name, oid), 'ab')
                        marshal.dump(refs, f)
                        f.close()
                else:
                    # deleted record
                    if good.has(name, oid):
                        good.remove(name, oid)
                    elif bad.has(name, oid):
                        bad.remove(name, oid)
                        path = bad_path(baddir, name, oid)
                        if os.path.exists(path):
                            os.remove(path)
                    deleted.insert(name, oid)

    if conf2 is not None:
        for db in db2.databases.itervalues():
            db.close()

    # Now, we have the garbage in bad.  Remove it.
    for name, db in db1.databases.iteritems():
        storage = db.storage
        t = transaction.begin()
        storage.tpc_begin(t)
        nd = 0
        for oid in bad.iterator(name):
            p, s = storage.load(oid, '')
            storage.deleteObject(oid, s, t)
            nd += 1
        logger.info("Removed %s objects from %s", nd, name)
        if nd:
            storage.tpc_vote(t)
            storage.tpc_finish(t)
            t.commit()
        else:
            storage.tpc_abort(t)
            t.abort()
        db.close()

    shutil.rmtree(baddir)

    return bad

def bad_path(baddir, name, oid):
    return os.path.join(baddir, name, base64.urlsafe_b64encode(oid))

def bad_to_good(baddir, bad, good, name, oid):
    bad.remove(name, oid)

    path = bad_path(baddir, name, oid)
    if not os.path.exists(path):
        return

    f = open(path , 'rb')
    while 1:
        try:
            refs = marshal.load(f)
        except EOFError:
            break

        for ref in refs:
            if good.insert(*ref) and bad.has(*ref):
                bad_to_good(baddir, bad, good, *ref)

    f.close()
    os.remove(path)

def getrefs(p, rname):
    refs = []
    u = cPickle.Unpickler(cStringIO.StringIO(p))
    u.persistent_load = refs
    u.noload()
    u.noload()
    for ref in refs:
        name = rname
        if isinstance(ref, tuple):
            yield rname, ref[0]
        elif isinstance(ref, str):
            yield rname, ref
        else:
            assert isinstance(ref, list)
            yield ref[1][:2]

class oidset(dict):

    def __init__(self, names):
        for name in names:
            self[name] = {}

    def insert(self, name, oid):
        ioid1, ioid2 = divmod(u64(oid), 2147483648L)
        ioid2 = int(ioid2)
        data = self[name].get(ioid1)
        if data is None:
            data = self[name][ioid1] = BTrees.IIBTree.TreeSet()
        elif ioid2 in data:
            return False
        data.insert(ioid2)
        return True

    def remove(self, name, oid):
        ioid1, ioid2 = divmod(u64(oid), 2147483648L)
        ioid2 = int(ioid2)
        data = self[name].get(ioid1)
        if data and ioid2 in data:
            data.remove(ioid2)
            if not data:
                del self[name][ioid1]

    def has(self, name, oid):
        ioid1, ioid2 = divmod(u64(oid), 2147483648L)
        data = self[name].get(ioid1)
        return bool(data and (int(ioid2) in data))

    def iterator(self, name=None):
        if name is None:
            for name in self:
                for oid in self.iterator(name):
                    yield name, oid
        else:
            for ioid1, data in self[name].iteritems():
                ioid1 *= 2147483648L
                for ioid2 in data:
                    yield p64(ioid1+ioid2)

def gc_command(args=None):
    if args is None:
        args = sys.argv[1:]
    parser = optparse.OptionParser("usage: %prog [options] config1 [config2]")
    parser.add_option(
        '-d', '--days', dest='days', type='int', default=1,
        help='Number of trailing days to treat as non-garbage')

    options, args = parser.parse_args(args)

    if not args or len(args) > 2:
        parser.parse_args(['-h'])

    return gc(args[0], options.days, *args[1:])

def check(config):
    db = ZODB.config.databaseFromFile(open(config))
    databases = db.databases
    storages = dict((name, db.storage) for (name, db) in databases.iteritems())
    roots = set((name, z64) for name in databases)
    referers = {}
    seen = oidset(databases)
    while roots:
        name, oid = roots.pop()
        if not seen.insert(name, oid):
            continue

        try:
            p, tid = storages[name].load(oid, '')
        except:
            print '!!!', name, u64(oid),
            referer = referers.pop((name, oid), None)
            if referer:
                rname, roid = referer
                print rname, u64(roid)
            else:
                print '?'
            continue

        referers.pop((name, oid), None)

        for ref in getrefs(p, name):
            if seen.has(*ref):
                continue
            if ref in roots:
                continue
            roots.add(ref)
            referers[ref] = name, oid

    [d.close() for d in db.databases.values()]

def check_command(args=None):
    if args is None:
        args = sys.argv[1:]
    parser = optparse.OptionParser("usage: %prog [options] config")
    options, args = parser.parse_args(args)

    if not args or len(args) > 1:
        parser.parse_args(['-h'])

    check(args[0])
