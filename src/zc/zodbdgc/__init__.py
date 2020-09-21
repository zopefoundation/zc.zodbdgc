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

from __future__ import print_function

from io import BytesIO
import logging
import marshal
import optparse
import struct
import sys
import tempfile
import time

from ZODB.utils import z64
import BTrees.fsBTree
import BTrees.OOBTree
import BTrees.LLBTree
from persistent import TimeStamp
import transaction
import ZODB.blob
import ZODB.config
import ZODB.FileStorage
import ZODB.fsIndex
import ZODB.POSException
import ZODB.serialize
from ZODB.Connection import TransactionMetaData

# For consistency and easy of distribution, always use zodbpickle. On
# most platforms, including PyPy, and all CPython >= 2.7 or 3, we need
# it because of missing or broken `noload` support. We could get away
# without it under CPython 2.6, but there's not really any point. Use
# the fastest version we can have access to (PyPy doesn't have
# fastpickle)
try:
    from zodbpickle.fastpickle import Unpickler
except ImportError:
    from zodbpickle.pickle import Unpickler

# In cases where we might iterate multiple times
# over large-ish dictionaries, avoid excessive copies
# that tend toward O(n^2) complexity by using the explicit
# iteration functions on Python 2
if hasattr(dict(), 'iteritems'):
    def _iteritems(d):
        return d.iteritems()

    def _itervalues(d):
        return d.itervalues()
else:
    def _iteritems(d):
        return d.items()

    def _itervalues(d):
        return d.values()

def p64(v):
    """Pack an integer or long into a 8-byte string"""
    return struct.pack(b">q", v)

def u64(v):
    """Unpack an 8-byte string into a 64-bit signed long integer."""
    return struct.unpack(b">q", v)[0]

logger = logging.getLogger(__name__)
log_format = "%(asctime)s %(name)s %(levelname)s: %(message)s"

def gc_command(args=None, ptid=None, return_bad=False):
    # The setuptools entry point for running a garbage collection.
    # Arguments and keyword arguments are for internal use only and
    # may change at any time. The return value is only defined when
    # return_bad is set to True; see :func:`gc`

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
        '-f', '--file-storage', dest='fs', action='append',
        help='name=path, use the given file storage path for analysis of the.'
             'named database')
    parser.add_option(
        '-i', '--ignore-database', dest='ignore', action='append',
        help='Ignore references to the given database name.')
    parser.add_option(
        '-l', '--log-level', dest='level',
        help='The logging level. The default is WARNING.')
    parser.add_option(
        '-u', '--untransform', dest='untransform',
        help='Function (module:expr) used to untransform data records in'
        ' files identified using the -file-storage/-f option')

    options, args = parser.parse_args(args)

    if not args or len(args) > 2:
        parser.parse_args(['-h'])
    elif len(args) == 2:
        conf2 = args[1]
    else:
        conf2 = None

    if options.level:
        level = options.level

    if level:
        try:
            level = int(level)
        except ValueError:
            level = getattr(logging, level)
        logging.basicConfig(level=level, format=log_format)

    untransform = options.untransform
    if untransform is not None:
        mod, expr = untransform.split(':', 1)
        untransform = eval(expr, __import__(mod, {}, {}, ['*']).__dict__)

    return gc(args[0], options.days, options.ignore or (), conf2=conf2,
              fs=dict(o.split('=') for o in options.fs or ()),
              untransform=untransform, ptid=ptid, return_bad=return_bad)


def gc(conf, days=1, ignore=(), conf2=None, fs=(), untransform=None,
       ptid=None, return_bad=False):
    # The programmatic entry point for running a GC. Internal function
    # only, all arguments and return values may change at any time.
    close = []
    result = None
    try:
        bad = gc_(close, conf, days, ignore, conf2, fs, untransform, ptid)
        if return_bad:
            # For tests only, we return a sorted list of the human readable
            # pairs (dbname, badoid) when requested. Bad will be closed
            # in the following finally block.
            result = sorted((name, int(u64(oid))) for (name, oid)
                            in bad.iterator())
        return result
    finally:
        for thing in close:
            if hasattr(thing, 'databases'):
                for db in thing.databases.values():
                    db.close()
            elif hasattr(thing, 'close'):
                thing.close()


def gc_(close, conf, days, ignore, conf2, fs, untransform, ptid):
    FileIterator = ZODB.FileStorage.FileIterator
    if untransform is not None:
        def FileIterator(*args):
            def transit(trans):
                for record in trans:
                    if record.data:
                        record.data = untransform(record.data)
                    yield record
            zfsit = ZODB.FileStorage.FileIterator(*args)
            try:
                for t in zfsit:
                    yield transit(t)
            finally:
                zfsit.close()

    def iter_storage(name, storage, start=None, stop=None):
        fsname = name or ''
        if fsname in fs:
            it = FileIterator(fs[fsname], start, stop)
        else:
            it = storage.iterator(start, stop)
        # We need to be sure to always close iterators
        # in case we raise an exception
        close.append(it)
        return it

    with open(conf) as f:
        db1 = ZODB.config.databaseFromFile(f)
    close.append(db1)
    if conf2 is None:
        db2 = db1
    else:
        logger.info("Using secondary configuration, %s, for analysis", conf2)
        with open(conf2) as f:
            db2 = ZODB.config.databaseFromFile(f)
        close.append(db2)
        if set(db1.databases) != set(db2.databases):
            raise ValueError("primary and secondary databases don't match.")

    databases = db2.databases
    storages = sorted((name, d.storage) for (name, d) in databases.items())

    if ptid is None:
        ptid = TimeStamp.TimeStamp(
            *time.gmtime(time.time() - 86400 * days)[:6]
        ).raw()

    good = oidset(databases)
    bad = Bad(databases)
    close.append(bad)

    deleted = oidset(databases)

    for name, storage in storages:
        fsname = name or ''
        logger.info("%s: roots", fsname)
        # Make sure we can get the roots
        data, s = storage.load(z64, '')
        good.insert(name, z64)
        for ref in getrefs(data, name, ignore):
            good.insert(*ref)

        n = 0
        if days:
            # All non-deleted new records are good
            logger.info("%s: recent", name)

            for trans in iter_storage(name, storage, start=ptid):
                for record in trans:
                    if n and n % 10000 == 0:
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
                        for ref_name, ref_oid in getrefs(data, name, ignore):
                            if not deleted.has(ref_name, ref_oid):
                                good.insert(ref_name, ref_oid)
                                bad.remove(ref_name, ref_oid)
                    else:
                        # deleted record
                        deleted.insert(name, oid)
                        good.remove(name, oid)

    for name, storage in storages:
        # Now iterate over older records
        for trans in iter_storage(name, storage, start=None, stop=ptid):
            for record in trans:
                if n and n % 10000 == 0:
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
                        bad.insert(name, oid, record.tid,
                                   getrefs(data, name, ignore))

                else:
                    # deleted record
                    if good.has(name, oid):
                        good.remove(name, oid)
                    elif bad.has(name, oid):
                        bad.remove(name, oid)
                    deleted.insert(name, oid)

    if conf2 is not None:
        for db in db2.databases.values():
            db.close()
        close.remove(db2)

    # Now, we have the garbage in bad.  Remove it.
    batch_size = 100
    for name, db in sorted(db1.databases.items()):
        logger.info("%s: remove garbage", name)
        storage = db.storage
        nd = 0
        t = transaction.begin()
        txn_meta = TransactionMetaData()
        storage.tpc_begin(txn_meta)
        start = time.time()
        for oid, tid in bad.iterator(name):
            try:
                storage.deleteObject(oid, tid, txn_meta)
            except (ZODB.POSException.POSKeyError,
                    ZODB.POSException.ConflictError):
                continue
            nd += 1
            if (nd % batch_size) == 0:
                storage.tpc_vote(txn_meta)
                storage.tpc_finish(txn_meta)
                t.commit()
                logger.info("%s: deleted %s", name, nd)
                duration = time.time() - start
                time.sleep(duration * 2)
                batch_size = max(10, int(batch_size * .5 / duration))
                t = transaction.begin()
                txn_meta = TransactionMetaData()
                storage.tpc_begin(txn_meta)
                start = time.time()

        logger.info("Removed %s objects from %s", nd, name)
        if nd:
            storage.tpc_vote(txn_meta)
            storage.tpc_finish(txn_meta)
            t.commit()
        else:
            storage.tpc_abort(txn_meta)
            t.abort()

    return bad


def getrefs(p, rname, ignore):
    refs = []
    u = Unpickler(BytesIO(p))
    u.persistent_load = refs.append
    u.noload()
    u.noload()
    for ref in refs:
        # ref types are documented in ZODB.serialize
        if isinstance(ref, tuple):
            # (oid, class meta data)
            yield rname, ref[0]
        elif isinstance(ref, str):
            # oid
            yield rname, ref
        elif not ref:
            # Seen in corrupted databases
            raise ValueError("Unexpected empty reference")
        elif ref:
            assert isinstance(ref, list)
            # [reference type, args] or [oid]
            if len(ref) == 1:
                # Legacy persistent weak ref. Ignored, not a
                # strong ref.
                # To treat as strong: yield rname, ref
                continue

            # Args is always a tuple, but depending on
            # the value of the reference type, the order
            # may be different. Types n and m are in the same
            # order, type w is different
            kind, ref = ref

            if kind in ('n', 'm'):
                # (dbname, oid, [class meta])
                if ref[0] not in ignore:
                    yield ref[:2]
            elif kind == 'w':
                # Weak ref, either (oid) for this DB
                # or (oid, dbname) for other db. Both ignored.
                # To treat the first as strong:
                #   yield rname, ref[0]
                # To treat the second as strong:
                #   yield ref[1], ref[0]
                continue
            else:
                raise ValueError('Unknown persistent ref', kind, ref)

class oidset(dict):
    """
    {(name, oid)} implemented as:

       {name-> {oid[:6] -> {oid[-2:]}}}
    """
    def __init__(self, names):
        for name in names:
            self[name] = {}

    def insert(self, name, oid):
        prefix = oid[:6]
        suffix = oid[6:]
        data = self[name].get(prefix)
        if data is None:
            data = self[name][prefix] = BTrees.fsBTree.TreeSet()
        elif suffix in data:
            return False
        data.insert(suffix)
        return True

    def remove(self, name, oid):
        prefix = oid[:6]
        suffix = oid[6:]
        data = self[name].get(prefix)
        if data and suffix in data:
            data.remove(suffix)
            if not data:
                del self[name][prefix]

    def __nonzero__(self):
        for v in _itervalues(self):
            if v:
                return True
        return False
    __bool__ = __nonzero__

    def pop(self):
        for name, data in _iteritems(self):
            if data:
                break
        prefix, s = next(iter(_iteritems(data)))
        suffix = s.maxKey()
        s.remove(suffix)
        if not s:
            del data[prefix]
        return name, prefix + suffix

    def has(self, name, oid):
        try:
            data = self[name][oid[:6]]
        except KeyError:
            return False
        return oid[6:] in data

    def iterator(self, name=None):
        if name is None:
            for name in self:
                for oid in self.iterator(name):
                    yield name, oid
        else:
            for prefix, data in _iteritems(self[name]):
                for suffix in data:
                    yield prefix + suffix

class Bad(object):

    def __init__(self, names):
        self._file = tempfile.TemporaryFile(dir='.', prefix='gcbad')
        self.close = self._file.close
        self._pos = 0
        self._dbs = {}
        for name in names:
            self._dbs[name] = ZODB.fsIndex.fsIndex()

    def remove(self, name, oid):
        db = self._dbs[name]
        if oid in db:
            del db[oid]

    def __nonzero__(self):
        raise SystemError('wtf')
        return sum(map(bool, _itervalues(self._dbs)))
    __bool__ = __nonzero__

    def has(self, name, oid):
        db = self._dbs[name]
        return oid in db

    def iterator(self, name=None):
        if name is None:
            for name in self._dbs:
                for oid in self._dbs[name]:
                    yield name, oid
        else:
            f = self._file
            for oid, pos in _iteritems(self._dbs[name]):
                f.seek(pos)
                yield oid, f.read(8)

    def insert(self, name, oid, tid, refs):
        assert len(tid) == 8
        f = self._file
        db = self._dbs[name]
        pos = db.get(oid)
        if pos is not None:
            f.seek(pos)
            oldtid = f.read(8)
            oldrefs = set(marshal.load(f))
            refs = oldrefs.union(refs)
            tid = max(tid, oldtid)
            if refs == oldrefs:
                if tid != oldtid:
                    f.seek(pos)
                    f.write(tid)
                return

        db[oid] = pos = self._pos
        f.seek(pos)
        f.write(tid)
        marshal.dump(list(refs), f)
        self._pos = f.tell()

    def pop(self, name, oid):
        db = self._dbs[name]
        pos = db.get(oid, None)
        if pos is None:
            return ()
        del db[oid]
        f = self._file
        f.seek(pos + 8)
        return marshal.load(f)


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
        conn.close()
        fs.close()

def _insert_ref(references, rname, roid, name, oid):
    if references is None:
        return False
    oid = u64(oid)
    roid = u64(roid)
    by_oid = references.get(name)
    if not by_oid:
        by_oid = references[name] = BTrees.LOBTree.BTree()
        # Setting _p_changed is needed when using older versions of
        # the pure-python BTree package (e.g., under PyPy). This is
        # a bug documented at https://github.com/zopefoundation/BTrees/pull/11.
        # Without it, the References example in README.test fails
        # with a KeyError: the 'db2' key is not found because it wasn't
        # persisted to disk.
        references._p_changed = True
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
        references._p_changed = True
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
                rname = next(iter(by_rname))
                return rname, p64(next(iter(by_rname[rname])))
            else:
                return name, p64(next(iter(by_rname)))

def check_(config, references=None):
    with open(config) as f:
        db = ZODB.config.databaseFromFile(f)
    try:
        databases = db.databases
        storages = dict((name, db.storage) for (name, db) in databases.items())

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
                p, tid = storages[name].load(oid, b'')
                if (# XXX should be in is_blob_record
                    len(p) < 100 and (b'ZODB.blob' in p)
                        and ZODB.blob.is_blob_record(p)
                ):
                    storages[name].loadBlob(oid, tid)
            except:
                print('!!!', name, u64(oid), end=' ')

                referer = _get_referer(references, name, oid)
                if referer:
                    rname, roid = referer
                    print(rname, u64(roid))
                else:
                    print('?')
                t, v = sys.exc_info()[:2]
                print("%s: %s" % (t.__name__, v))
                continue

            for ref in getrefs(p, name, ()):
                if (ref[0] != name) and not databases[name].xrefs:
                    print('bad xref', ref[0], u64(ref[1]), name, u64(oid))

                nreferences += _insert_ref(references, name, oid, *ref)

                if nreferences > 400:
                    transaction.commit()
                    nreferences = 0

                if ref[0] not in databases:
                    print('!!!', ref[0], u64(ref[1]), name, u64(oid))
                    print('bad db')
                    continue
                if seen.has(*ref):
                    continue
                roots.insert(*ref)
    finally:
        for d in db.databases.values():
            d.close()

def check_command(args=None):
    if args is None:
        args = sys.argv[1:]
        logging.basicConfig(level=logging.WARNING, format=log_format)

    parser = optparse.OptionParser("usage: %prog [options] config")
    parser.add_option(
        '-r', '--references-filestorage', dest='refdb',
        help='The name of a file-storage to save reference info in.')

    options, args = parser.parse_args(args)

    if not args or len(args) > 1:
        parser.parse_args(['-h'])

    check(args[0], options.refdb)

class References(object):

    def __init__(self, db):
        self._conn = ZODB.connection(db)
        self._refs = self._conn.root.references

    def close(self):
        self._conn.close()

    def __getitem__(self, arg):
        name, oid = arg
        if isinstance(oid, (str, bytes)):
            oid = u64(oid)
        by_rname = self._refs[name][oid]
        if isinstance(by_rname, dict):
            for rname, roids in _iteritems(by_rname):
                for roid in roids:
                    yield rname, roid
        else:
            for roid in by_rname:
                yield name, roid
