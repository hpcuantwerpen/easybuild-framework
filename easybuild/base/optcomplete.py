# External compatible license
# ******************************************************************************\
# * Copyright (c) 2003-2004, Martin Blais
# * All rights reserved.
# *
# * Redistribution and use in source and binary forms, with or without
# * modification, are permitted provided that the following conditions are
# * met:
# *
# * * Redistributions of source code must retain the above copyright
# *   notice, this list of conditions and the following disclaimer.
# *
# * * Redistributions in binary form must reproduce the above copyright
# *   notice, this list of conditions and the following disclaimer in the
# *   documentation and/or other materials provided with the distribution.
# *
# * * Neither the name of the Martin Blais, Furius, nor the names of its
# *   contributors may be used to endorse or promote products derived from
# *   this software without specific prior written permission.
# *
# * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# ******************************************************************************\

"""Automatic completion for optparse module.

This module provide automatic bash completion support for programs that use the
optparse module.  The premise is that the optparse options parser specifies
enough information (and more) for us to be able to generate completion strings
esily.  Another advantage of this over traditional completion schemes where the
completion strings are hard-coded in a separate bash source file, is that the
same code that parses the options is used to generate the completions, so the
completions is always up-to-date with the program itself.

In addition, we allow you specify a list of regular expressions or code that
define what kinds of files should be proposed as completions to this file if
needed.  If you want to implement more complex behaviour, you can instead
specify a function, which will be called with the current directory as an
argument.

You need to activate bash completion using the shell script function that comes
with optcomplete (see http://furius.ca/optcomplete for more details).

Authors:

* Martin Blais (blais@furius.ca)
* Stijn De Weirdt (Ghent University)

This is a copy of optcomplete.py (changeset 17:e0a9131a94cc)
from source: https://hg.furius.ca/public/optcomplete

Modification by stdweird:
    - cleanup
"""

# Bash Protocol Description
# -------------------------
# 'COMP_CWORD'
#      An index into `${COMP_WORDS}' of the word containing the current
#      cursor position.  This variable is available only in shell
#      functions invoked by the programmable completion facilities (*note
#      Programmable Completion::).
#
# 'COMP_LINE'
#      The current command line.  This variable is available only in
#      shell functions and external commands invoked by the programmable
#      completion facilities (*note Programmable Completion::).
#
# 'COMP_POINT'
#      The index of the current cursor position relative to the beginning
#      of the current command.  If the current cursor position is at the
#      end of the current command, the value of this variable is equal to
#      `${#COMP_LINE}'.  This variable is available only in shell
#      functions and external commands invoked by the programmable
#      completion facilities (*note Programmable Completion::).
#
# 'COMP_WORDS'
#      An array variable consisting of the individual words in the
#      current command line.  This variable is available only in shell
#      functions invoked by the programmable completion facilities (*note
#      Programmable Completion::).
#
# 'COMPREPLY'
#      An array variable from which Bash reads the possible completions
#      generated by a shell function invoked by the programmable
#      completion facility (*note Programmable Completion::).


import copy
import glob
import logging
import os
import re
import shlex
import sys
import types

from optparse import OptionParser, Option
from pprint import pformat

from easybuild.tools.filetools import get_cwd
from easybuild.tools.utilities import shell_quote

debugfn = None  # for debugging only

OPTCOMPLETE_ENVIRONMENT = 'OPTPARSE_AUTO_COMPLETE'

BASH = "bash"

DEFAULT_SHELL = BASH

SHELL = DEFAULT_SHELL

OPTION_CLASS = Option
OPTIONPARSER_CLASS = OptionParser


def set_optionparser(option_class, optionparser_class):
    """Set the default Option and OptionParser class"""
    global OPTION_CLASS
    global OPTIONPARSER_CLASS
    OPTION_CLASS = option_class
    OPTIONPARSER_CLASS = optionparser_class


def get_shell():
    """Determine the shell, update class constant SHELL and return the shell
    Idea is to call it just once
    """
    global SHELL
    SHELL = os.path.basename(os.environ.get("SHELL", DEFAULT_SHELL))
    return SHELL


# get the shell
get_shell()


class CompleterMissingCallArgument(Exception):
    """Exception to raise when call arg is missing"""


class Completer(object):
    """Base class to derive all other completer classes from.
    It generates an empty completion list
    """
    CALL_ARGS = None  # list of named args that must be passed
    CALL_ARGS_OPTIONAL = None  # list of named args that can be passed

    def __call__(self, **kwargs):
        """Check mandatory args, then return _call"""
        all_args = []
        if self.CALL_ARGS is not None:
            for arg in self.CALL_ARGS:
                all_args.append(arg)
                if arg not in kwargs:
                    msg = "%s __call__ missing mandatory arg %s" % (self.__class__.__name__, arg)
                    raise CompleterMissingCallArgument(msg)

        if self.CALL_ARGS_OPTIONAL is not None:
            all_args.extend(self.CALL_ARGS_OPTIONAL)

        for arg in kwargs.keys():
            if arg not in all_args:
                # remove it
                kwargs.pop(arg)

        return self._call(**kwargs)

    def _call(self, **kwargs):  # pylint: disable=unused-argument
        """Return empty list"""
        return []


class NoneCompleter(Completer):
    """Generates empty completion list. For compatibility reasons."""
    pass


class ListCompleter(Completer):
    """Completes by filtering using a fixed list of strings."""

    def __init__(self, stringlist):
        self.olist = stringlist

    def _call(self, **kwargs):
        """Return the initialised fixed list of strings"""
        return map(str, self.olist)


class AllCompleter(Completer):
    """Completes by listing all possible files in current directory."""
    CALL_ARGS_OPTIONAL = ['pwd']

    def _call(self, **kwargs):
        return os.listdir(kwargs.get('pwd', '.'))


class FileCompleter(Completer):
    """Completes by listing all possible files in current directory.
       If endings are specified, then limit the files to those."""
    CALL_ARGS_OPTIONAL = ['prefix']

    def __init__(self, endings=None):
        if isinstance(endings, str):
            endings = [endings]
        elif endings is None:
            endings = []
        self.endings = tuple(map(str, endings))

    def _call(self, **kwargs):
        # TODO : what does prefix do in bash?
        prefix = kwargs.get('prefix', '')

        if SHELL == BASH:
            res = ['_filedir']
            if self.endings:
                res.append("'@(%s)'" % '|'.join(self.endings))
            return " ".join(res)
        else:
            res = []
            for path in glob.glob(prefix + '*'):
                res.append(path)
                if os.path.isdir(path):
                    # add trailing slashes to directories
                    res[-1] += os.path.sep

            if self.endings:
                res = [path for path in res if os.path.isdir(path) or path.endswith(self.endings)]

            if len(res) == 1 and os.path.isdir(res[0]):
                # return two options so that it completes the / but doesn't add a space
                return [res[0] + 'a', res[0] + 'b']
            else:
                return res


class DirCompleter(Completer):
    """Completes by listing subdirectories only."""
    CALL_ARGS_OPTIONAL = ['prefix']

    def _call(self, **kwargs):
        # TODO : what does prefix do in bash?
        prefix = kwargs.get('prefix', '')

        if SHELL == BASH:
            return "_filedir -d"
        else:
            res = [path + "/" for path in glob.glob(prefix + '*') if os.path.isdir(path)]

            if len(res) == 1:
                # return two options so that it completes the / but doesn't add a space
                return [res[0] + 'a', res[0] + 'b']
            else:
                return res


class KnownHostsCompleter(Completer):
    """Completes a list of known hostnames"""

    def _call(self, **kwargs):
        if SHELL == BASH:
            return "_known_hosts"
        else:
            # TODO needs implementation, no autocompletion for now
            return []


class RegexCompleter(Completer):
    """Completes by filtering all possible files with the given list of regexps."""
    CALL_ARGS_OPTIONAL = ['prefix', 'pwd']

    def __init__(self, regexlist, always_dirs=True):
        self.always_dirs = always_dirs

        if isinstance(regexlist, str):
            regexlist = [regexlist]
        self.regexlist = []
        for regex in regexlist:
            if isinstance(regex, str):
                regex = re.compile(regex)
            self.regexlist.append(regex)

    def _call(self, **kwargs):
        dn = os.path.dirname(kwargs.get('prefix', ''))
        if dn:
            pwd = dn
        else:
            pwd = kwargs.get('pwd', '.')

        ofiles = []
        for fn in os.listdir(pwd):
            for r in self.regexlist:
                if r.match(fn):
                    if dn:
                        fn = os.path.join(dn, fn)
                    ofiles.append(fn)
                    break

            if self.always_dirs and os.path.isdir(fn):
                ofiles.append(fn + os.path.sep)

        return ofiles


class CompleterOption(OPTION_CLASS):
    """optparse Option class with completer attribute"""

    def __init__(self, *args, **kwargs):
        completer = kwargs.pop('completer', None)
        OPTION_CLASS.__init__(self, *args, **kwargs)
        if completer is not None:
            self.completer = completer


def extract_word(line, point):
    """Return a prefix and suffix of the enclosing word.  The character under
    the cursor is the first character of the suffix."""

    if SHELL == BASH and 'IFS' in os.environ:
        ifs = [r.group(0) for r in re.finditer(r'.', os.environ['IFS'])]
        wsre = re.compile('|'.join(ifs))
    else:
        wsre = re.compile(r'\s')

    if point < 0 or point > len(line):
        return '', ''

    preii = point - 1
    while preii >= 0:
        if wsre.match(line[preii]):
            break
        preii -= 1
    preii += 1

    sufii = point
    while sufii < len(line):
        if wsre.match(line[sufii]):
            break
        sufii += 1

    return line[preii:point], line[point:sufii]


def error_override(self, msg):
    """Hack to keep OptionParser from writing to sys.stderr when
    calling self.exit from self.error"""
    self.exit(2, msg=msg)


def guess_first_nonoption(gparser, subcmds_map):
    """Given a global options parser, try to guess the first non-option without
    generating an exception. This is used for scripts that implement a
    subcommand syntax, so that we can generate the appropriate completions for
    the subcommand."""

    gparser = copy.deepcopy(gparser)

    def print_usage_nousage(self, *args, **kwargs):  # pylint: disable=unused-argument
        pass
    gparser.print_usage = print_usage_nousage

    prev_interspersed = gparser.allow_interspersed_args  # save state to restore
    gparser.disable_interspersed_args()

    # interpret cwords like a shell would interpret it
    cwords = shlex.split(os.environ.get('COMP_WORDS', '').strip('() '))

    # save original error_func so we can put it back after the hack
    error_func = gparser.error
    try:
        try:
            instancemethod = type(OPTIONPARSER_CLASS.error)
            # hack to keep OptionParser from writing to sys.stderr
            gparser.error = instancemethod(error_override, gparser, OPTIONPARSER_CLASS)
            _, args = gparser.parse_args(cwords[1:])
        except SystemExit:
            return None
    finally:
        # undo the hack and restore original OptionParser error function
        gparser.error = instancemethod(error_func, gparser, OPTIONPARSER_CLASS)

    value = None
    if args:
        subcmdname = args[0]
        try:
            value = subcmds_map[subcmdname]
        except KeyError:
            pass

    gparser.allow_interspersed_args = prev_interspersed  # restore state

    return value  # can be None, indicates no command chosen.


def autocomplete(parser, arg_completer=None, opt_completer=None, subcmd_completer=None, subcommands=None):
    """Automatically detect if we are requested completing and if so generate
    completion automatically from given parser.

    'parser' is the options parser to use.

    'arg_completer' is a callable object that gets invoked to produce a list of
    completions for arguments completion (oftentimes files).

    'opt_completer' is the default completer to the options that require a
    value.

    'subcmd_completer' is the default completer for the subcommand
    arguments.

    If 'subcommands' is specified, the script expects it to be a map of
    command-name to an object of any kind.  We are assuming that this object is
    a map from command name to a pair of (options parser, completer) for the
    command. If the value is not such a tuple, the method
    'autocomplete(completer)' is invoked on the resulting object.

    This will attempt to match the first non-option argument into a subcommand
    name and if so will use the local parser in the corresponding map entry's
    value.  This is used to implement completion for subcommand syntax and will
    not be needed in most cases.
    """

    # If we are not requested for complete, simply return silently, let the code
    # caller complete. This is the normal path of execution.
    if OPTCOMPLETE_ENVIRONMENT not in os.environ:
        return
    # After this point we should never return, only sys.exit(1)

    # Set default completers.
    if arg_completer is None:
        arg_completer = NoneCompleter()
    if opt_completer is None:
        opt_completer = FileCompleter()
    if subcmd_completer is None:
        # subcmd_completer = arg_completer
        subcmd_completer = FileCompleter()

    # By default, completion will be arguments completion, unless we find out
    # later we're trying to complete for an option.
    completer = arg_completer

    #
    # Completing...
    #

    # Fetching inputs... not sure if we're going to use these.

    # zsh's bashcompinit does not pass COMP_WORDS, replace with
    # COMP_LINE for now...
    if 'COMP_WORDS' not in os.environ:
        os.environ['COMP_WORDS'] = os.environ['COMP_LINE']

    cwords = shlex.split(os.environ.get('COMP_WORDS', '').strip('() '))
    cline = os.environ.get('COMP_LINE', '')
    cpoint = int(os.environ.get('COMP_POINT', 0))
    cword = int(os.environ.get('COMP_CWORD', 0))

    # Extract word enclosed word.
    prefix, suffix = extract_word(cline, cpoint)

    # If requested, try subcommand syntax to find an options parser for that
    # subcommand.
    if subcommands:
        assert isinstance(subcommands, dict)
        value = guess_first_nonoption(parser, subcommands)
        if value:
            if isinstance(value, (list, tuple)):
                parser = value[0]
                if len(value) > 1 and value[1]:
                    # override completer for command if it is present.
                    completer = value[1]
                else:
                    completer = subcmd_completer
                autocomplete(parser, completer)
            elif hasattr(value, 'autocomplete'):
                # Call completion method on object. This should call
                # autocomplete() recursively with appropriate arguments.
                value.autocomplete(subcmd_completer)
            else:
                # no completions for that command object
                pass
            sys.exit(1)
        else:  # suggest subcommands
            completer = ListCompleter(subcommands.keys())

    # Look at previous word, if it is an option and it requires an argument,
    # check for a local completer.  If there is no completer, what follows
    # directly cannot be another option, so mark to not add those to
    # completions.
    optarg = False
    try:
        # Look for previous word, which will be containing word if the option
        # has an equals sign in it.
        prev = None
        if cword < len(cwords):
            mo = re.search('(--.*?)=(.*)', cwords[cword])
            if mo:
                prev, prefix = mo.groups()
        if not prev:
            prev = cwords[cword - 1]

        if prev and prev.startswith('-'):
            option = parser.get_option(prev)
            if option:
                if option.nargs > 0:
                    optarg = True
                    try:
                        completer = option.completer
                    except AttributeError:
                        if option.choices:
                            completer = ListCompleter(option.choices)
                        elif option.type in ('string',):
                            completer = opt_completer
                        else:
                            completer = NoneCompleter()
                # Warn user at least, it could help him figure out the problem.
                elif hasattr(option, 'completer'):
                    msg = "Error: optparse option with a completer does not take arguments: %s" % (option)
                    raise SystemExit(msg)
    except KeyError:
        pass

    completions = []

    # Options completion.
    if not optarg and (not prefix or prefix.startswith('-')):
        completions += parser._short_opt.keys()
        completions += parser._long_opt.keys()
        # Note: this will get filtered properly below.

    completer_kwargs = {
        'pwd': get_cwd(),
        'cline': cline,
        'cpoint': cpoint,
        'prefix': prefix,
        'suffix': suffix,
    }
    # File completion.
    if completer and (not prefix or not prefix.startswith('-')):
        # Call appropriate completer depending on type.
        if isinstance(completer, (str, list, tuple)):
            completer = FileCompleter(completer)
        elif not isinstance(completer, (types.FunctionType, types.LambdaType, types.ClassType, types.ObjectType)):
            # TODO: what to do here?
            pass

        completions = completer(**completer_kwargs)

    if isinstance(completions, str):
        # is a bash command, just run it
        if SHELL in (BASH,):  # TODO: zsh
            print(completions)
        else:
            raise Exception("Commands are unsupported by this shell %s" % SHELL)
    else:
        # Filter using prefix.
        if prefix:
            completions = sorted(filter(lambda x: x.startswith(prefix), completions))
        completions = ' '.join(map(str, completions))

        # Save results
        if SHELL == "bash":
            print('COMPREPLY=(' + completions + ')')
        else:
            print(completions)

    # Print debug output (if needed).  You can keep a shell with 'tail -f' to
    # the log file to monitor what is happening.
    if debugfn:
        txt = "\n".join([
            '---------------------------------------------------------',
            'CWORDS %s' % cwords,
            'CLINE %s' % cline,
            'CPOINT %s' % cpoint,
            'CWORD %s' % cword,
            '',
            'Short options',
            pformat(parser._short_opt),
            '',
            'Long options',
            pformat(parser._long_opt),
            'Prefix %s' % prefix,
            'Suffix %s' % suffix,
            'completer_kwargs%s' % str(completer_kwargs),
            # 'completer_completions %s' % completer_completions,
            'completions %s' % completions,
        ])
        if isinstance(debugfn, logging.Logger):
            debugfn.debug(txt)
        else:
            with open(debugfn, 'a') as fh:
                fh.write(txt)

    # Exit with error code (we do not let the caller continue on purpose, this
    # is a run for completions only.)
    sys.exit(1)


class CmdComplete(object):

    """Simple default base class implementation for a subcommand that supports
    command completion.  This class is assuming that there might be a method
    addopts(self, parser) to declare options for this subcommand, and an
    optional completer data member to contain command-specific completion.  Of
    course, you don't really have to use this, but if you do it is convenient to
    have it here."""

    def autocomplete(self, completer=None):
        parser = OPTIONPARSER_CLASS(self.__doc__.strip())
        if hasattr(self, 'addopts'):
            self.addopts(parser)

        completer = getattr(self, 'completer', completer)

        return autocomplete(parser, completer)


def gen_cmdline(cmd_list, partial, shebang=True):
    """Create the commandline to generate simulated tabcompletion output
    :param cmd_list: command to execute as list of strings
    :param partial: the string to autocomplete (typically, partial is an element of the cmd_list)
    :param shebang: script has python shebang (if not, add sys.executable)
    """
    cmdline = ' '.join([shell_quote(cmd) for cmd in cmd_list])

    env = []
    env.append("%s=1" % OPTCOMPLETE_ENVIRONMENT)
    env.append('COMP_LINE="%s"' % cmdline)
    env.append('COMP_WORDS="(%s)"' % cmdline)
    env.append("COMP_POINT=%s" % len(cmdline))
    env.append("COMP_CWORD=%s" % cmd_list.index(partial))

    if not shebang:
        env.append(sys.executable)

    # add script
    env.append('"%s"' % cmd_list[0])

    return " ".join(env)
