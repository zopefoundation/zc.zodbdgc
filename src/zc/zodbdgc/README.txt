ZODB Distributed GC
===================

This package provides 2 scripts, for multi-database garbage collection
and database validation.

The scripts require that the databases provided to them use 64-bit
object ids.  The garbage-collection script also assumes that the
databases support efficient iteration from transactions near the end
of the databases.

multi-zodb-gc
-------------

The multi-zodb-gc script takes one or 2 configuration files.  If a
single configuration file is given, garbage collection is performed on
the databases specified by the configuration files.  If garbage is
found, then delete records are written to the databases.  When the
databases are subsequently packed to a time after the delete records
are written, the garbage objects will be removed.

If a second configuration file is given, then the databases specified
in the second configuration file will be used to find garbage.
Deleted records are still written to the databases given in the first
configuration file.  When using replicated-database technology,
analysis can be performed using secondary storages, which are usually
lightly loaded.  This is helpful because finding garbage places a
significant load on the databases used to find garbage.

If your database uses file-storages, then rather than specifying a
second configuration file, you can use the -f option to specify
file-storage iterators for finding garbage.  Using file storage
iterators is much faster than using a ZEO connection and is faster and
requires less memory than opening a read-only file storage on the files.

Some number of trailing days (1 by default) of database records are
considered good, meaning the objects referenced by them are not
garbage. This allows the garbage-collection algorithm to work more
efficiently and avoids problems when applications (incorrectly) do
things that cause objects to be temporarily unreferenced, such as
moving objects in 2 transactions.

Options can be used to control the number of days of trailing data to
be treated as non garbage and to specify the logging level.  Use the
``--help`` option to get details.


multi-zodb-check-refs
---------------------

The multi-zodb-check-refs script validates a collection of databases
by starting with their roots and traversing the databases to make sure
all referenced objects are reachable.  Any unreachable objects are
reported. If any databases are configured to disallow implicit
cross-database references, then invalid references are reported as
well.  Blob records are checked to make sure their blob files can be
loaded.

Optionally, a database of reference information can be generated. This
database allows you to find objects referencing a given object id in a
database. This can be very useful to debugging missing objects.
Generation of the references database increases the analysis time
substantially. The references database can become quite large, often a
substantial percentage of the size of the databases being analyzed.
Typically, you'll perform an initial analysis without a references
database and only create a references file in a subsequent run if
problems are found.

You can run the script with the ``--help`` option to get usage
information.

Change History
==============

0.6.1 2012-10-08
----------------

Fixed: GC could fail it special cases with a NameError.

0.6.0 2010-05-27
----------------

- Added support for storages with transformed (e.g. compressed) data
  records.

0.5.0 2009-11-10
----------------

- Fixed a bug in the delay throttle that made it delete objects way
  too slowly.

0.4.0 2009-09-08
----------------

- The previous version deleted too many objects at a time, which could
  put too much load on a heavily loaded storage server.

  - Add a sleep or allow the storage to rest after a set of deletions.
    Sleep for twice the time taken to perform the deletions.

  - Adjust the deletion batch size to take about .5 seconds per
    batch of deletions, but do at least 10 at a time.

0.3.0 2009-09-03
----------------

- Optimized garbage collection by using a temporary file to
  store object references rather than loading them from the analysis
  database when needed.

- Added an -f option to specify file-storage files directly.  It is
  wildly faster to iterate over a file storage than over a ZEO
  connection.  Using this option uses a file iterator rather than
  opening a file storage in read-only mode, which avoids scanning the
  database to build an index and avoids the memory cost of a
  file-storage index.

0.2.0 2009-06-15
----------------

- Added an option to ignore references to some databases.

- Fixed a bug in handling of the logging level option.

0.1.0 2009-06-11
----------------

Initial release
