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

from ZODB.utils import z64
import BTrees.fsBTree
import BTrees.OOBTree
import BTrees.LLBTree
import base64
import bsddb
import cPickle
import cStringIO
import itertools
import logging
import marshal
import optparse
import os
import shutil
import struct
import sys
import tempfile
import time
import transaction
import ZODB.blob
import ZODB.config
import ZODB.FileStorage
import ZODB.TimeStamp

def p64(v):
    """Pack an integer or long into a 8-byte string"""
    return struct.pack(">q", v)

def u64(v):
    """Unpack an 8-byte string into a 64-bit signed long integer."""
    return struct.unpack(">q", v)[0]

logger = logging.getLogger(__name__)

def gc(conf, days=1, ignore=(), conf2=None, batch_size=10000):
    close = []
    try:
        return gc_(close, conf, days, ignore, conf2, batch_size)
    finally:
        for db in close:
            for db in db.databases.itervalues():
                db.close()

def gc_(close, conf, days, ignore, conf2, batch_size):
    db1 = ZODB.config.databaseFromFile(open(conf))
    close.append(db1)
    if conf2 is None:
        db2 = db1
    else:
        logger.info("Using secondary configuration, %s, for analysis", conf2)
        db2 = ZODB.config.databaseFromFile(open(conf2))
        close.append(db1)
        if set(db1.databases) != set(db2.databases):
            raise ValueError("primary and secondary databases don't match.")

    databases = db2.databases
    storages = sorted((name, d.storage) for (name, d) in databases.items())

    ptid = repr(
        ZODB.TimeStamp.TimeStamp(*time.gmtime(time.time() - 86400*days)[:6])
        )

    good = oidset(databases)
    bad = Bad(databases)
    deleted = oidset(databases)

    for name, storage in storages:
        logger.info("%s: roots", name)
        # Make sure we can get the roots
        data, s = storage.load(z64, '')
        good.insert(name, z64)
        for ref in getrefs(data, name, ignore):
            good.insert(*ref)

        n = 0
        if days:
            # All non-deleted new records are good
            logger.info("%s: recent", name)
            for trans in storage.iterator(ptid):
                for record in trans:
                    if n and n%10000 == 0:
                        logger.info("%s: %s recent", name, n)
                    n += 1

                    oid = record.oid
                    data = record.data
                    if data:
                        if deleted.has(name, oid):
                            raise AssertionError(
                                "Non-deleted record after deleted")
                        good.insert(name, oid)

                        # and anything they reference
                        for ref in getrefs(data, name, ignore):
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
                if n and n%10000 == 0:
                    logger.info("%s: %s old", name, n)
                n += 1

                oid = record.oid
                data = record.data
                if data:
                    if deleted.has(name, oid):
                        continue
                    if good.has(name, oid):
                        for ref in getrefs(data, name, ignore):
                            if deleted.has(*ref):
                                continue
                            if good.insert(*ref) and bad.has(*ref):
                                to_do = [ref]
                                while to_do:
                                    for ref in bad.pop(*to_do.pop()):
                                        if good.insert(*ref) and bad.has(*ref):
                                            to_do.append(ref)
                    else:
                        bad.insert(name, oid, set(getrefs(data, name, ignore)))
                else:
                    # deleted record
                    if good.has(name, oid):
                        good.remove(name, oid)
                    elif bad.has(name, oid):
                        bad.remove(name, oid)
                    deleted.insert(name, oid)

    if conf2 is not None:
        for db in db2.databases.itervalues():
            db.close()
        close.pop()

    good.close()
    deleted.close()

    # Now, we have the garbage in bad.  Remove it.
    for name, db in sorted(db1.databases.iteritems()):
        logger.info("%s: remove garbage", name)
        storage = db.storage
        t = transaction.begin()
        storage.tpc_begin(t)
        nd = 0
        for oid in bad.iterator(name):
            p, s = storage.load(oid, '')
            storage.deleteObject(oid, s, t)
            nd += 1
            if (nd % batch_size) == 0:
                storage.tpc_vote(t)
                storage.tpc_finish(t)
                t.commit()
                logger.info("%s: deleted %s", name, nd)
                t = transaction.begin()
                storage.tpc_begin(t)

        logger.info("Removed %s objects from %s", nd, name)
        if nd:
            storage.tpc_vote(t)
            storage.tpc_finish(t)
            t.commit()
        else:
            storage.tpc_abort(t)
            t.abort()

    return bad

def getrefs(p, rname, ignore):
    refs = []
    u = cPickle.Unpickler(cStringIO.StringIO(p))
    u.persistent_load = refs
    u.noload()
    u.noload()
    for ref in refs:
        if isinstance(ref, tuple):
            yield rname, ref[0]
        elif isinstance(ref, str):
            yield rname, ref
        else:
            assert isinstance(ref, list)
            ref = ref[1]
            if ref[0] not in ignore:
                yield ref[:2]

class oidset:

    def __init__(self, names):
        self._dbs = {}
        self._paths = []
        for name in names:
            fd, path = tempfile.mkstemp(dir='.')
            os.close(fd)
            self._dbs[name] = bsddb.hashopen(path)
            self._paths.append(path)

    def close(self):
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()
        while self._paths:
            os.remove(self._paths.pop())

    def insert(self, name, oid):
        db = self._dbs[name]
        if oid in db:
            return False
        db[oid] = ''
        return True

    def remove(self, name, oid):
        db = self._dbs[name]
        if oid in db:
            del db[oid]

    def pop(self):
        for name, db in self._dbs.iteritems():
            if db:
                oid, _ = db.popitem()
                return name, oid

    def __nonzero__(self):
        return sum(map(bool, self._dbs.itervalues()))

    def has(self, name, oid):
        db = self._dbs[name]
        return oid in db

    def iterator(self, name=None):
        if name is None:
            for name in self._dbs:
                for oid in self._dbs[name]:
                    yield name, oid
        else:
            for oid in self._dbs[name]:
                yield oid

class Bad(oidset):

    def insert(self, name, oid, refs):
        db = self._dbs[name]
        old = db.get(oid)
        if old is None:
            db[oid] = refs and marshal.dumps(list(refs)) or ''
        else:
            if old:
                if refs:
                    old = set(marshal.loads(old))
                    refs = old.union(refs)
                    if refs != old:
                        db[oid] = marshal.dumps(list(refs))
            elif refs:
                db[oid] = marshal.dumps(list(refs))

    def pop(self, name, oid):
        refs = self._dbs[name].pop(oid)
        if refs:
            return marshal.loads(refs)
        return ()


def gc_command(args=None):
    if args is None:
        args = sys.argv[1:]
        level = logging.WARNING
    else:
        level = None

    parser = optparse.OptionParser("usage: %prog [options] config1 [config2]")
    parser.add_option(
        '-d', '--days', dest='days', type='int', default=1,
        help='Number of trailing days (defaults to 1) to treat as non-garbage')
    parser.add_option(
        '-i', '--ignore-database', dest='ignore', action='append',
        help='Ignore references to the given database name.')
    parser.add_option(
        '-l', '--log-level', dest='level',
        help='The logging level. The default is WARNING.')

    options, args = parser.parse_args(args)

    if not args or len(args) > 2:
        parser.parse_args(['-h'])

    if options.level:
        level = options.level

    if level:
        try:
            level = int(level)
        except ValueError:
            level = getattr(logging, level)
        logging.basicConfig(level=level)

    return gc(args[0], options.days, options.ignore or (), *args[1:])



def check(config, refdb=None):
    if refdb is None:
        return check_(config)

    fs = ZODB.FileStorage.FileStorage(refdb, create=True)
    conn = ZODB.connection(fs)
    references = conn.root.references = BTrees.OOBTree.BTree()
    try:
        check_(config, references)
    finally:
        transaction.commit()
        fs.close()

def _insert_ref(references, rname, roid, name, oid):
    if references is None:
        return False
    oid = u64(oid)
    roid = u64(roid)
    by_oid = references.get(name)
    if not by_oid:
        by_oid = references[name] = BTrees.LOBTree.BTree()
    by_rname = by_oid.get(oid)
    if not by_rname:
        references = BTrees.LLBTree.TreeSet()
        if rname == name:
            by_oid[oid] = references
        else:
            by_oid[oid] = {rname: references}
    elif isinstance(by_rname, dict):
        references = by_rname.get(rname)
        if not references:
            references = by_rname[rname] = BTrees.LLBTree.TreeSet()
            # trigger change since dict is not persistent:
            by_oid[oid] = by_rname
    elif rname != name:
        references = BTrees.LLBTree.TreeSet()
        by_oid[oid] = {name: by_rname, rname: references}
    else:
        references = by_rname

    if roid not in references:
        references.insert(roid)
        return True
    return False

def _get_referer(references, name, oid):
    if references is None:
        return
    by_oid = references.get(name)
    if by_oid:
        oid = u64(oid)
        by_rname = by_oid.get(oid)
        if by_rname:
            if isinstance(by_rname, dict):
                rname = iter(by_rname).next()
                return rname, p64(iter(by_rname[rname]).next())
            else:
                return name, p64(iter(by_rname).next())

def check_(config, references=None):
    db = ZODB.config.databaseFromFile(open(config))
    databases = db.databases
    storages = dict((name, db.storage) for (name, db) in databases.iteritems())

    roots = oidset(databases)
    for name in databases:
        roots.insert(name, z64)
    seen = oidset(databases)
    nreferences = 0

    while roots:
        name, oid = roots.pop()

        try:
            if not seen.insert(name, oid):
                continue
            p, tid = storages[name].load(oid, '')
            if (
                # XXX should be in is_blob_record
                len(p) < 100 and ('ZODB.blob' in p)

                and ZODB.blob.is_blob_record(p)
                ):
                storages[name].loadBlob(oid, tid)
        except:
            print '!!!', name, u64(oid),

            referer = _get_referer(references, name, oid)
            if referer:
                rname, roid = referer
                print rname, u64(roid)
            else:
                print '?'
            t, v = sys.exc_info()[:2]
            print "%s: %s" % (t.__name__, v)
            continue

        for ref in getrefs(p, name, ()):
            if (ref[0] != name) and not databases[name].xrefs:
                print 'bad xref', ref[0], u64(ref[1]), name, u64(oid)

            nreferences += _insert_ref(references, name, oid, *ref)

            if nreferences > 400:
                transaction.commit()
                nreferences = 0

            if ref[0] not in databases:
                print '!!!', ref[0], u64(ref[1]), name, u64(oid)
                print 'bad db'
                continue
            if seen.has(*ref):
                continue
            roots.insert(*ref)

    [d.close() for d in db.databases.values()]

def check_command(args=None):
    if args is None:
        args = sys.argv[1:]
        logging.basicConfig(level=logging.WARNING)

    parser = optparse.OptionParser("usage: %prog [options] config")
    parser.add_option(
        '-r', '--references-filestorage', dest='refdb',
        help='The name of a file-storage to save reference info in.')

    options, args = parser.parse_args(args)

    if not args or len(args) > 1:
        parser.parse_args(['-h'])

    check(args[0], options.refdb)

class References:

    def __init__(self, db):
        self._conn = ZODB.connection(db)
        self._refs = self._conn.root.references

    def __getitem__(self, (name, oid)):
        if isinstance(oid, str):
            oid = u64(oid)
        by_rname = self._refs[name][oid]
        if isinstance(by_rname, dict):
            for rname, roids in by_rname.iteritems():
                for roid in roids:
                    yield rname, roid
        else:
            for roid in by_rname:
                yield name, roid
