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
import cPickle
import cStringIO
import logging
import marshal
import optparse
import struct
import sys
import tempfile
import time
import transaction
import ZODB.blob
import ZODB.config
import ZODB.FileStorage
import ZODB.fsIndex
import ZODB.POSException
import ZODB.TimeStamp

def p64(v):
    """Pack an integer or long into a 8-byte string"""
    return struct.pack(">q", v)

def u64(v):
    """Unpack an 8-byte string into a 64-bit signed long integer."""
    return struct.unpack(">q", v)[0]

logger = logging.getLogger(__name__)
log_format = "%(asctime)s %(name)s %(levelname)s: %(message)s"

def gc_command(args=None, ptid=None):
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
        conf2=args[1]
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
              untransform=untransform, ptid=ptid)


def gc(conf, days=1, ignore=(), conf2=None, fs=(), untransform=None, ptid=None):
    close = []
    try:
        return gc_(close, conf, days, ignore, conf2, fs, untransform, ptid)
    finally:
        for db in close:
            for db in db.databases.itervalues():
                db.close()

def gc_(close, conf, days, ignore, conf2, fs, untransform, ptid):

    FileIterator = ZODB.FileStorage.FileIterator
    if untransform is not None:
        def FileIterator(*args):
            def transit(trans):
                for record in trans:
                    if record.data:
                        record.data = untransform(record.data)
                    yield record
            for t in ZODB.FileStorage.FileIterator(*args):
                yield transit(t)

    db1 = ZODB.config.databaseFromFile(open(conf))
    close.append(db1)
    if conf2 is None:
        db2 = db1
    else:
        logger.info("Using secondary configuration, %s, for analysis", conf2)
        db2 = ZODB.config.databaseFromFile(open(conf2))
        close.append(db2)
        if set(db1.databases) != set(db2.databases):
            raise ValueError("primary and secondary databases don't match.")

    databases = db2.databases
    storages = sorted((name, d.storage) for (name, d) in databases.items())

    if ptid is None:
        ptid = repr(
            ZODB.TimeStamp.TimeStamp(
                *time.gmtime(time.time() - 86400*days)[:6]
                ))

    good = oidset(databases)
    bad = Bad(databases)
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

            if fsname in fs:
                it = FileIterator(fs[fsname], ptid)
            else:
                it = storage.iterator(ptid)

            for trans in it:
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
        if fsname in fs:
            it = FileIterator(fs[fsname], None, ptid)
        else:
            it = storage.iterator(None, ptid)

        for trans in it:
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
        for db in db2.databases.itervalues():
            db.close()
        close.pop()

    # Now, we have the garbage in bad.  Remove it.
    batch_size = 100
    for name, db in sorted(db1.databases.iteritems()):
        logger.info("%s: remove garbage", name)
        storage = db.storage
        nd = 0
        t = transaction.begin()
        storage.tpc_begin(t)
        start = time.time()
        for oid, tid in bad.iterator(name):
            try:
                storage.deleteObject(oid, tid, t)
            except (ZODB.POSException.POSKeyError,
                    ZODB.POSException.ConflictError):
                continue
            nd += 1
            if (nd % batch_size) == 0:
                storage.tpc_vote(t)
                storage.tpc_finish(t)
                t.commit()
                logger.info("%s: deleted %s", name, nd)
                duration = time.time()-start
                time.sleep(duration*2)
                batch_size = max(10, int(batch_size*.5/duration))
                t = transaction.begin()
                storage.tpc_begin(t)
                start = time.time()

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
        for v in self.itervalues():
            if v:
                return True
        return False

    def pop(self):
        for name, data in self.iteritems():
            if data:
               break
        prefix, s = data.iteritems().next()
        suffix = s.maxKey()
        s.remove(suffix)
        if not s:
            del data[prefix]
        return name, prefix+suffix

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
            for prefix, data in self[name].iteritems():
                for suffix in data:
                    yield prefix+suffix

class Bad:

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
            f = self._file
            for oid, pos in self._dbs[name].iteritems():
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
        f.seek(pos+8)
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
        logging.basicConfig(level=logging.WARNING, format=log_format)

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
