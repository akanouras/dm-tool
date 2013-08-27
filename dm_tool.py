#! /usr/bin/env python
# -*- coding:utf-8 -*-
from __future__ import print_function  # , unicode_literals

# Based on dm-tool.c from the LightDM project.
# Original Author: Robert Ancell <robert.ancell@canonical.com>
# Copyright (C) 2013 Antonis Kanouras <antonis@metadosis.eu>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. See http://www.gnu.org/copyleft/gpl.html the full text of the
# license.

# Constants
__version__ = '1.6.0.metadosis0'
__all__ = ['DMTool']

COMMANDS_HELP = '''\
Commands:
  switch-to-greeter                   Switch to the greeter
  switch-to-user USERNAME [SESSION]   Switch to a user session
  switch-to-guest [SESSION]           Switch to a guest session
  lock                                Lock the current seat
  list-seats                          List the active seats
  add-nested-seat [XEPHYR_ARGS...]    Start a nested display
  add-local-x-seat DISPLAY_NUMBER     Add a local X seat
  add-seat TYPE [NAME=VALUE...]       Add a dynamic seat
'''

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

import os
import sys
import errno
import signal
import traceback
import argparse
import collections
import itertools
from io import StringIO
import dbus

# Python 3 compatibility
try:
    unicode
except NameError:
    unicode = str
u = unicode

FORMATS = collections.defaultdict(lambda: ("{0}{1}={2}", lambda x: x))
FORMATS.update({
    # DBus type: (format, formatter)
    dbus.String: ("{0}{1}='{2}'", lambda x: x),
    dbus.Boolean: ("{0}{1}={2}", lambda x: u(bool(x)).lower()),
})


def print_item(key, value, indent=0, file=None):
    fmt, formatter = FORMATS[type(value)]
    print(u(fmt).format(' ' * indent, key, formatter(value)), file=file)


def get_free_display_number():
    '''Get a unique display number.

    It's racy, but the only reliable method to get one.'''

    for display_number in itertools.count():
        try:
            os.stat('/tmp/.X{0}-lock'.format(display_number))
        except OSError as e:
            if e.errno == errno.ENOENT:
                return display_number
            else:
                raise


class DMTool(object):
    __doc__ = COMMANDS_HELP

    class _BaseDBusProxies(dict):
        'Dict of proxy: (object_path, interface)'

    _dbus_proxies = _BaseDBusProxies({
        'dm': ('/org/freedesktop/DisplayManager',
               'org.freedesktop.DisplayManager'),
        'seat': ('/org/freedesktop/DisplayManager/Seat',
                 'org.freedesktop.DisplayManager.Seat'),
        'session': ('/org/freedesktop/DisplayManager/Session',
                    'org.freedesktop.DisplayManager.Session')
    })

    class _BaseDBusMethods(dict):
        'Dict of method: proxy'

    _dbus_methods = _BaseDBusMethods({
        'SwitchToGreeter': 'seat',
        'SwitchToUser': 'seat',
        'SwitchToGuest': 'seat',
        'Lock': 'seat',
        'AddLocalXSeat': 'dm',
        'AddSeat': 'dm',
    })

    def __init__(self, bus=None):
        'bus must be a dbus.*Bus instance'
        if bus is None:
            bus = dbus.SystemBus()
        self._bus = bus

    def __call__(self, command, *args, **kwargs):
        'Call a command argv-style, see self.__doc__ for details'
        command = getattr(self, command.replace('-', '_'))
        return command(*args, **kwargs)

    def _dbus_call(self, method, *args, **kwargs):
        'Call one of the predefined dbus methods'
        method_type = self._dbus_methods[method]
        object_path, interface = self._dbus_proxies[method_type]
        if method_type == 'seat':
            try:
                new_object_path = os.environ['XDG_SEAT_PATH']
                if new_object_path.startswith(object_path):
                    object_path = new_object_path
                else:
                    raise KeyError
            except KeyError as e:
                raise StandardError('Not running inside a display manager,'
                                    ' XDG_SEAT_PATH is invalid or not defined')
        proxy = self._bus.get_object('org.freedesktop.DisplayManager',
                                     object_path)
        method = proxy.get_dbus_method(
            method,
            dbus_interface=interface)
        return method(*args, **kwargs)

    @classmethod
    def _get_commands(self):
        'Returns a dict of command: description'
        return {cmd.replace('_', '-'): getattr(self, cmd).__doc__
                for cmd in dir(self) if not cmd.startswith('_')}

    def switch_to_greeter(self):
        'Switch to the greeter'
        return self._dbus_call('SwitchToGreeter')

    def switch_to_user(self, username, session=None):
        'Switch to a user session'
        return self._dbus_call('SwitchToUser', username, session or '')

    def switch_to_guest(self, session=None):
        'Switch to a guest session'
        return self._dbus_call('SwitchToGuest', session or '')

    def lock(self):
        'Lock the current seat'
        return self._dbus_call('Lock')

    def list_seats(self):
        'List the active seats'

        def get_proxy(path):
            return self._bus.get_object('org.freedesktop.DisplayManager', path)

        def get_properties(proxy):
            path = proxy.object_path.rstrip('0123456789')
            interfaces = dict(self._dbus_proxies.values())
            interface = interfaces[path]
            return proxy.GetAll(interface, dbus_interface=dbus.PROPERTIES_IFACE)

        def get_name_from_path(path):
            return path.split('/org/freedesktop/DisplayManager/')[-1]

        output = StringIO()

        dm_proxy = get_proxy('/org/freedesktop/DisplayManager')
        seats = get_properties(dm_proxy)['Seats']

        for seat in seats:
            seat_name = get_name_from_path(seat)
            seat_proxy = get_proxy(seat)

            print(u('{0}').format(seat_name), file=output)

            seat_properties = get_properties(seat_proxy)
            for key, value in sorted(seat_properties.items()):
                if key == 'Sessions':
                    continue
                print_item(key, value, indent=2, file=output)

            sessions = seat_properties['Sessions']

            for session in sessions:
                session_name = get_name_from_path(session)
                session_proxy = get_proxy(session)

                print(u('  {0}').format(session_name), file=output)

                session_properties = get_properties(session_proxy)
                for key, value in sorted(session_properties.items()):
                    if key == 'Seat':
                        continue
                    print_item(key, value, indent=4, file=output)

        return output.getvalue().rstrip('\n')

    def add_nested_seat(self, *xephyr_args):
        'Start a nested display'

        def xephyr_signal_cb(sig, frame):
            try:
                self._sighandler_result = self._dbus_call(
                    'AddLocalXSeat', xephyr_display_number)
            except Exception as e:
                self._sighandler_result = 'Unable to add seat: {0}'.format(e)
                os.kill(xephyr_pid, signal.SIGQUIT)
                raise StandardError('Unable to add seat: {0}'.format(e))

        for arg in xephyr_args:
            if arg.startswith(':'):
                try:
                    xephyr_display_number = int(arg.lstrip(':'))
                except ValueError:
                    continue
                xephyr_argv = ['xephyr']
                break
        else:
            xephyr_display_number = get_free_display_number()
            xephyr_argv = ['Xephyr', ':{0}'.format(xephyr_display_number)]

        xephyr_argv.extend(xephyr_args)

        # Wait for signal from Xephyr when it is ready
        signal.signal(signal.SIGUSR1, xephyr_signal_cb)

        xephyr_pid = os.fork()
        if xephyr_pid == 0:
            # In child
            os.closerange(0, 1024)
            # This makes Xephyr SIGUSR1 its parent when ready.
            signal.signal(signal.SIGUSR1, signal.SIG_IGN)
            try:
                os.execlp(xephyr_argv[0], *xephyr_argv)
            except OSError as e:
                sys.exit(e.errno)
            except:
                # All file descriptors are closed, oh well.
                sys.exit(os.EX_OSERR)

        try:
            os.waitpid(xephyr_pid, 0)
        except OSError as e:
            if e.errno == errno.EINTR:
                # Signal handler returned
                result = self._sighandler_result
            else:
                raise

        try:
            return result
        except UnboundLocalError:
            # Xephyr failed to launch for whatever reason.
            raise StandardError('Xephyr launch failed')

    def add_local_x_seat(self, display_number):
        'Add a local X seat'
        return self._dbus_call('AddLocalXSeat', int(display_number))

    def add_seat(self, type, *args, **kwargs):
        'Add a dynamic seat'

        # AddSeat expects a list of tuples
        properties = [tuple(arg.split('=', 1))
                      if not isinstance(arg, tuple) else arg
                      for arg in args] + kwargs.items()

        return self._dbus_call('AddSeat', type, properties)


def get_parser():
    parser = argparse.ArgumentParser(
        description='Display Manager tool',
        usage='%(prog)s [OPTION...] COMMAND [ARGS...]',
        epilog=COMMANDS_HELP,
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter)

    options = parser.add_argument_group('Options')
    options.add_argument('-h', '--help', help='Show help options',
                         action='help')
    options.add_argument('-v', '--version', help='Show release version',
                         action='version',
                         version='%(prog)s {0}'.format(__version__))
    options.add_argument('--debug', dest='debug',
                         action='store_true',
                         help='Show debugging information')
    options.add_argument('--session-bus', dest='session_bus',
                         action='store_true',
                         help='Use session D-Bus')

    parser.add_argument('command', metavar='COMMAND',
                        choices=DMTool._get_commands(), help=argparse.SUPPRESS)
    parser.add_argument('rest', metavar='ARGS', nargs='*',
                        help=argparse.SUPPRESS)

    return parser


def main():
    parser = get_parser()
    args, unparsed = parser.parse_known_args()
    command_args = args.rest + unparsed

    bus = dbus.SessionBus() if args.session_bus else dbus.SystemBus()
    dmtool = DMTool(bus)

    try:
        print(dmtool(args.command, *command_args) or '')
    except Exception as e:
        if args.debug:
            traceback.print_exc()
        else:
            print(e, file=sys.stderr)
        if isinstance(e, TypeError):
            parser.print_help()
            return os.EX_USAGE
        else:
            return EXIT_FAILURE

if __name__ == '__main__':
    sys.exit(main())
