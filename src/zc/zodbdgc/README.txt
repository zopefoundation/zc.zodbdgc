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
