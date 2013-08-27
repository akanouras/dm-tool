dm-tool
=======

Python port of LightDM's dm-tool.  
Copyright (C) 2013 [Antonis Kanouras](mailto:antonis@metadosis.eu)

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You can find a copy of the GNU General Public License in the LICENSE
    file distributed with this program or at http://www.gnu.org/licenses/

dm-tool.c author: [Robert Ancell](mailto:robert.ancell@canonical.com)
Please don't mail him with questions about this project.

Requirements
------------

* Python 2.7 or 3.x
* dbus-python (usually packaged as python{,3}-dbus)

Usage
-----

    % dm_tool.py --help
    usage: dm_tool.py [OPTION...] COMMAND [ARGS...]

    Display Manager tool

    Options:
      -h, --help     Show help options
      -v, --version  Show release version
      --debug        Show debugging information
      --session-bus  Use session D-Bus

    Commands:
      switch-to-greeter                   Switch to the greeter
      switch-to-user USERNAME [SESSION]   Switch to a user session
      switch-to-guest [SESSION]           Switch to a guest session
      lock                                Lock the current seat
      list-seats                          List the active seats
      add-nested-seat [XEPHYR_ARGS...]    Start a nested display
      add-local-x-seat DISPLAY_NUMBER     Add a local X seat
      add-seat TYPE [NAME=VALUE...]       Add a dynamic seat

Or, programmatically:

    from dm_tool import DMTool

    dmtool = DMTool()
    seat_path = dmtool.add_nested_seat('-screen', '1366x768x32')
    print(seat_path)

All DMTool class members not starting with an underscore are considered public API.

TODO
----

* Package for PyPI
* Rewrite the argv parser using getopt
* Take advantage of Xephyr 1.13+ -displayfd
* Cover the full org.freedesktop.DisplayManager API
* Better error reporting
* Replace under/overengineered pieces of code
