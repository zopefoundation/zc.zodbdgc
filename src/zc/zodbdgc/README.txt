ZODB Distributed GC
===================

This package provides a script for performing distributed garbage
collection for a collection of ZODB storages, which will typically be
ZEO clients.

Note that this script will likely be included in future ZODB
releases. It's being developed independently now because it is new and
we don't want to be limited by or to affect the ZODB release cycle.

The script takes the fillowing options:

-d n, --days n

   Provide the number of days in the past to garbage collect to.  And
   objects written after than number of days will be considered to be
   non garbage.  This defaults to 3.

-s config, --storage config

   The name of a configuration file defining storages to be garbage
   collected.

-a config, --analyze config

   The name of a configuration file defining storage servers to use
   for analysis.  This is useful with replicated storages, as it
   allows analysis to take place using stprage servers that are under
   lighter load.  If not provided, then the storages specified using
   the --storage option are used for analysis.
