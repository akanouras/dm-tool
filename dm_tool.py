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


class DBusFormats(collections.defaultdict):
    'Dict of dbus.types.*: (format, formatter)'

    default_factory = lambda: ("{0}{1}={2}", lambda x: x)

    default_formats = {
        dbus.String: ("{0}{1}='{2}'", lambda x: x),
        dbus.Boolean: ("{0}{1}={2}", lambda x: u(bool(x)).lower()),
    }

    def __init__(self, default_format=None, default_formats=None):
        if default_format is not None:
            self.default_factory = default_format
        if default_formats is not None:
            self.default_formats = default_formats
        self.update(self.default_formats)


class DMTool(object):
    __doc__ = COMMANDS_HELP

    # Dict of method: path
    _dbus_paths = {
        'SwitchToGreeter': '/org/freedesktop/DisplayManager/Seat',
        'SwitchToUser': '/org/freedesktop/DisplayManager/Seat',
        'SwitchToGuest': '/org/freedesktop/DisplayManager/Seat',
        'Lock': '/org/freedesktop/DisplayManager/Seat',
        'AddLocalXSeat': '/org/freedesktop/DisplayManager',
        'AddSeat': '/org/freedesktop/DisplayManager',
    }

    _dbus_formats = DBusFormats()

    def __init__(self, bus=None):
        'bus must be a dbus.*Bus instance'
        if not os.environ.get('XDG_SEAT_PATH', '').startswith(
                '/org/freedesktop/DisplayManager/Seat'):
            raise Exception('Not running inside a display manager,'
                                ' XDG_SEAT_PATH is invalid or not defined')
        if bus is None:
            bus = dbus.SystemBus()
        self._bus = bus

    def __call__(self, command, *args, **kwargs):
        'Call a command argv-style, see self.__doc__ for details'
        command = getattr(self, command.replace('-', '_'))
        return command(*args, **kwargs)

    @staticmethod
    def _path_to_interface(path):
        return path.rstrip('0123456789').lstrip('/').replace('/', '.')

    def _get_proxy(self, path):
        return self._bus.get_object('org.freedesktop.DisplayManager', path)

    def _dbus_call(self, method, *args, **kwargs):
        'Call one of the predefined dbus methods'
        object_path = self._dbus_paths[method]
        interface = self._path_to_interface(object_path)
        if object_path == '/org/freedesktop/DisplayManager/Seat':
            object_path = os.environ['XDG_SEAT_PATH']
        proxy = self._get_proxy(object_path)
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

        def get_properties(proxy):
            interface = self._path_to_interface(proxy.object_path)
            return proxy.GetAll(interface, dbus_interface=dbus.PROPERTIES_IFACE)

        def get_name_from_path(path):
            return path.split('/org/freedesktop/DisplayManager/')[-1]

        def print_item(key, value, indent=0, file=None):
            fmt, formatter = self._dbus_formats[type(value)]
            print(u(fmt).format(' ' * indent, key, formatter(value)), file=file)

        def print_path(path, exclude=None, indent=0, file=None):
            path_proxy = self._get_proxy(path)

            path_name = get_name_from_path(path)
            print(u('{0}{1}').format(' ' * indent, path_name), file=file)
            indent += 2

            descend_paths = []
            path_properties = get_properties(path_proxy)
            for key, value in sorted(path_properties.items()):
                if value == exclude:
                    continue
                if isinstance(value, dbus.Array):
                    if len(value) > 0 and isinstance(value[0], dbus.ObjectPath):
                        descend_paths += value
                    continue
                print_item(key, value, indent=indent, file=file)

            for descend_path in descend_paths:
                print_path(descend_path, exclude=path, indent=indent, file=file)

        output = StringIO()

        dm_proxy = self._get_proxy('/org/freedesktop/DisplayManager')
        seats = get_properties(dm_proxy)['Seats']

        for seat in sorted(seats):
            print_path(seat, file=output)

        return output.getvalue().rstrip('\n')

    def add_nested_seat(self, *xephyr_args):
        'Start a nested display'

        def xephyr_signal_handler(sig, frame):
            # Fugly, nonlocal (Py3K+) would make this prettier
            xephyr_signal_handler.was_called = True

        def setup_xephyr_handler():
            xephyr_signal_handler.original_handler = signal.getsignal(signal.SIGUSR1)
            xephyr_signal_handler.was_called = False
            signal.signal(signal.SIGUSR1, xephyr_signal_handler)

        def wait_for_xephyr(pid):
            try:
                os.waitpid(pid, 0)
            except:  # On purpose
                pass
            signal.signal(signal.SIGUSR1, xephyr_signal_handler.original_handler)
            return xephyr_signal_handler.was_called

        xephyr_argv = ['Xephyr']

        # Determine the display number to use for Xephyr
        for arg in xephyr_args:
            if arg.startswith(':'):
                try:
                    xephyr_display_number = int(arg.lstrip(':'))
                    break
                except ValueError:
                    continue
        else:
            xephyr_display_number = get_free_display_number()
            xephyr_argv += ':{0}'.format(xephyr_display_number)

        xephyr_argv.extend(xephyr_args)

        # Wait for signal from Xephyr when it is ready
        setup_xephyr_handler()

        # Spawn Xephyr
        xephyr_pid = os.fork()
        if xephyr_pid == 0:
            # In child
            os.closerange(0, 1023)
            # This makes X(ephyr) SIGUSR1 its parent when ready.
            signal.signal(signal.SIGUSR1, signal.SIG_IGN)
            try:
                os.execvp(xephyr_argv[0], xephyr_argv)
            except OSError as e:
                sys.exit(e.errno)

        # Wait for Xephyr to signal us
        if wait_for_xephyr(xephyr_pid):
            try:
                return self._dbus_call('AddLocalXSeat', xephyr_display_number)
            except Exception as e:
                os.kill(xephyr_pid, signal.SIGQUIT)
                raise Exception('Unable to add seat: {0}'.format(e))
        else:
            raise Exception('Xephyr launch failed')

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
            return 1

if __name__ == '__main__':
    sys.exit(main())
