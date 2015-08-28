================
 Change History
================

1.0.0 (2015-08-28)
==================

- Add support for PyPy, Python 2.7, and Python 3.
  This requires the addition of the ``zodbpickle`` dependency, even on
  Python 2.6.
- Fixed the ``--days`` argument to ``multi-zodb-gc`` with recent
  versions of ``persistent``.
- The return values and arguments of the internal implementation
  functions ``gc`` and ``gc_command`` have changed for compatibility
  with Python 3. This will not impact users of the documented scripts
  and is noted only for developers.

0.6.1 (2012-10-08)
==================

- Fixed: GC could fail it special cases with a NameError.

0.6.0 (2010-05-27)
==================

- Added support for storages with transformed (e.g. compressed) data
  records.

0.5.0 (2009-11-10)
==================

- Fixed a bug in the delay throttle that made it delete objects way
  too slowly.

0.4.0 (2009-09-08)
==================

- The previous version deleted too many objects at a time, which could
  put too much load on a heavily loaded storage server.

  - Add a sleep or allow the storage to rest after a set of deletions.
    Sleep for twice the time taken to perform the deletions.

  - Adjust the deletion batch size to take about .5 seconds per
    batch of deletions, but do at least 10 at a time.

0.3.0 (2009-09-03)
==================

- Optimized garbage collection by using a temporary file to
  store object references rather than loading them from the analysis
  database when needed.

- Added an -f option to specify file-storage files directly.  It is
  wildly faster to iterate over a file storage than over a ZEO
  connection.  Using this option uses a file iterator rather than
  opening a file storage in read-only mode, which avoids scanning the
  database to build an index and avoids the memory cost of a
  file-storage index.

0.2.0 (2009-06-15)
==================

- Added an option to ignore references to some databases.

- Fixed a bug in handling of the logging level option.

0.1.0 (2009-06-11)
==================

Initial release
