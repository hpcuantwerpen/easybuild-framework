# #
# -*- coding: utf-8 -*-
# Copyright 2012-2023 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
# #
"""
Unit tests for filetools.py

@author: Toon Willems (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Stijn De Weirdt (Ghent University)
"""
import contextlib
import glob
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
from test.framework.utilities import EnhancedTestCase, TestLoaderFiltered, init_config
from unittest import TextTestRunner
from easybuild.base.fancylogger import setLogLevelDebug

import easybuild.tools.asyncprocess as asyncprocess
import easybuild.tools.utilities
from easybuild.tools.build_log import EasyBuildError, init_logging, stop_logging
from easybuild.tools.config import update_build_option
from easybuild.tools.filetools import adjust_permissions, change_dir, mkdir, read_file, write_file
from easybuild.tools.run import RunShellCmdResult, check_async_cmd, check_log_for_errors, complete_cmd
from easybuild.tools.run import get_output_from_process, parse_log_for_error, run_shell_cmd, run_cmd, run_cmd_qa
from easybuild.tools.run import subprocess_terminate
from easybuild.tools.config import ERROR, IGNORE, WARN


class RunTest(EnhancedTestCase):
    """ Testcase for run module """

    def setUp(self):
        """Set up test."""
        super(RunTest, self).setUp()
        self.orig_experimental = easybuild.tools.utilities._log.experimental

    def tearDown(self):
        """Test cleanup."""
        super(RunTest, self).tearDown()

        # restore log.experimental
        easybuild.tools.utilities._log.experimental = self.orig_experimental

    def test_get_output_from_process(self):
        """Test for get_output_from_process utility function."""

        @contextlib.contextmanager
        def get_proc(cmd, asynchronous=False):
            if asynchronous:
                proc = asyncprocess.Popen(cmd, shell=True, stdout=asyncprocess.PIPE, stderr=asyncprocess.STDOUT,
                                          stdin=asyncprocess.PIPE, close_fds=True, executable='/bin/bash')
            else:
                proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.PIPE, close_fds=True, executable='/bin/bash')

            try:
                yield proc
            finally:
                # Make sure to close the process and its pipes
                subprocess_terminate(proc, timeout=1)

        # get all output at once
        with get_proc("echo hello") as proc:
            out = get_output_from_process(proc)
            self.assertEqual(out, 'hello\n')

        # first get 100 bytes, then get the rest all at once
        with get_proc("echo hello") as proc:
            out = get_output_from_process(proc, read_size=100)
            self.assertEqual(out, 'hello\n')
            out = get_output_from_process(proc)
            self.assertEqual(out, '')

        # get output in small bits, keep trying to get output (which shouldn't fail)
        with get_proc("echo hello") as proc:
            out = get_output_from_process(proc, read_size=1)
            self.assertEqual(out, 'h')
            out = get_output_from_process(proc, read_size=3)
            self.assertEqual(out, 'ell')
            out = get_output_from_process(proc, read_size=2)
            self.assertEqual(out, 'o\n')
            out = get_output_from_process(proc, read_size=1)
            self.assertEqual(out, '')
            out = get_output_from_process(proc, read_size=10)
            self.assertEqual(out, '')
            out = get_output_from_process(proc)
            self.assertEqual(out, '')

        # can also get output asynchronously (read_size is *ignored* in that case)
        async_cmd = "echo hello; read reply; echo $reply"

        with get_proc(async_cmd, asynchronous=True) as proc:
            out = get_output_from_process(proc, asynchronous=True)
            self.assertEqual(out, 'hello\n')
            asyncprocess.send_all(proc, 'test123\n')
            out = get_output_from_process(proc)
            self.assertEqual(out, 'test123\n')

        with get_proc(async_cmd, asynchronous=True) as proc:
            out = get_output_from_process(proc, asynchronous=True, read_size=1)
            # read_size is ignored when getting output asynchronously, we're getting more than 1 byte!
            self.assertEqual(out, 'hello\n')
            asyncprocess.send_all(proc, 'test123\n')
            out = get_output_from_process(proc, read_size=3)
            self.assertEqual(out, 'tes')
            out = get_output_from_process(proc, read_size=2)
            self.assertEqual(out, 't1')
            out = get_output_from_process(proc)
            self.assertEqual(out, '23\n')

    def test_run_cmd(self):
        """Basic test for run_cmd function."""
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd("echo hello")
        self.assertEqual(out, "hello\n")
        # no reason echo hello could fail
        self.assertEqual(ec, 0)
        self.assertEqual(type(out), str)

        # test running command that emits non-UTF-8 characters
        # this is constructed to reproduce errors like:
        # UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe2
        # UnicodeEncodeError: 'ascii' codec can't encode character u'\u2018'
        for text in [b"foo \xe2 bar", b"foo \u2018 bar"]:
            test_file = os.path.join(self.test_prefix, 'foo.txt')
            write_file(test_file, text)
            cmd = "cat %s" % test_file

            with self.mocked_stdout_stderr():
                (out, ec) = run_cmd(cmd)
            self.assertEqual(ec, 0)
            self.assertTrue(out.startswith('foo ') and out.endswith(' bar'))
            self.assertEqual(type(out), str)

    def test_run_shell_cmd_basic(self):
        """Basic test for run_shell_cmd function."""

        with self.mocked_stdout_stderr():
            res = run_shell_cmd("echo hello")
        self.assertEqual(res.output, "hello\n")
        # no reason echo hello could fail
        self.assertEqual(res.cmd, "echo hello")
        self.assertEqual(res.exit_code, 0)
        self.assertTrue(isinstance(res.output, str))
        self.assertEqual(res.stderr, None)
        self.assertTrue(res.work_dir and isinstance(res.work_dir, str))

        # test running command that emits non-UTF-8 characters
        # this is constructed to reproduce errors like:
        # UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe2
        # UnicodeEncodeError: 'ascii' codec can't encode character u'\u2018'
        # (such errors are ignored by the 'run' implementation)
        for text in [b"foo \xe2 bar", b"foo \u2018 bar"]:
            test_file = os.path.join(self.test_prefix, 'foo.txt')
            write_file(test_file, text)
            cmd = "cat %s" % test_file

            with self.mocked_stdout_stderr():
                res = run_shell_cmd(cmd)
            self.assertEqual(res.cmd, cmd)
            self.assertEqual(res.exit_code, 0)
            self.assertTrue(res.output.startswith('foo ') and res.output.endswith(' bar'))
            self.assertTrue(isinstance(res.output, str))
            self.assertTrue(res.work_dir and isinstance(res.work_dir, str))

    def test_run_cmd_log(self):
        """Test logging of executed commands."""
        fd, logfile = tempfile.mkstemp(suffix='.log', prefix='eb-test-')
        os.close(fd)

        regex = re.compile('cmd "echo hello" exited with exit code [0-9]* and output:')

        # command output is not logged by default without debug logging
        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            self.assertTrue(run_cmd("echo hello"))
        stop_logging(logfile)
        self.assertEqual(len(regex.findall(read_file(logfile))), 0)
        write_file(logfile, '')

        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            self.assertTrue(run_cmd("echo hello", log_all=True))
        stop_logging(logfile)
        self.assertEqual(len(regex.findall(read_file(logfile))), 1)
        write_file(logfile, '')

        # with debugging enabled, exit code and output of command should only get logged once
        setLogLevelDebug()

        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            self.assertTrue(run_cmd("echo hello"))
        stop_logging(logfile)
        self.assertEqual(len(regex.findall(read_file(logfile))), 1)
        write_file(logfile, '')

        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            self.assertTrue(run_cmd("echo hello", log_all=True))
        stop_logging(logfile)
        self.assertEqual(len(regex.findall(read_file(logfile))), 1)
        write_file(logfile, '')

        # Test that we can set the directory for the logfile
        log_path = os.path.join(self.test_prefix, 'chicken')
        mkdir(log_path)
        logfile = None
        init_logging(logfile, silent=True, tmp_logdir=log_path)
        logfiles = os.listdir(log_path)
        self.assertEqual(len(logfiles), 1)
        self.assertTrue(logfiles[0].startswith("easybuild"))
        self.assertTrue(logfiles[0].endswith("log"))

    def test_run_shell_cmd_log(self):
        """Test logging of executed commands with run_shell_cmd function."""

        fd, logfile = tempfile.mkstemp(suffix='.log', prefix='eb-test-')
        os.close(fd)

        regex_start_cmd = re.compile("Running command 'echo hello' in /")
        regex_cmd_exit = re.compile("Command 'echo hello' exited with exit code [0-9]* and output:")

        # command output is always logged
        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd("echo hello")
        stop_logging(logfile)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(len(regex_start_cmd.findall(read_file(logfile))), 1)
        self.assertEqual(len(regex_cmd_exit.findall(read_file(logfile))), 1)
        write_file(logfile, '')

        # with debugging enabled, exit code and output of command should only get logged once
        setLogLevelDebug()

        init_logging(logfile, silent=True)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd("echo hello")
        stop_logging(logfile)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(len(regex_start_cmd.findall(read_file(logfile))), 1)
        self.assertEqual(len(regex_cmd_exit.findall(read_file(logfile))), 1)
        write_file(logfile, '')

    def test_run_cmd_negative_exit_code(self):
        """Test run_cmd function with command that has negative exit code."""
        # define signal handler to call in case run_cmd takes too long
        def handler(signum, _):
            raise RuntimeError("Signal handler called with signal %s" % signum)

        orig_sigalrm_handler = signal.getsignal(signal.SIGALRM)

        try:
            # set the signal handler and a 3-second alarm
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(3)

            with self.mocked_stdout_stderr():
                (_, ec) = run_cmd("kill -9 $$", log_ok=False)
            self.assertEqual(ec, -9)

            # reset the alarm
            signal.alarm(0)
            signal.alarm(3)

            with self.mocked_stdout_stderr():
                (_, ec) = run_cmd_qa("kill -9 $$", {}, log_ok=False)
            self.assertEqual(ec, -9)

        finally:
            # cleanup: disable the alarm + reset signal handler for SIGALRM
            signal.signal(signal.SIGALRM, orig_sigalrm_handler)
            signal.alarm(0)

    def test_run_shell_cmd_fail(self):
        """Test run_shell_cmd function with command that has negative exit code."""
        # define signal handler to call in case run takes too long
        def handler(signum, _):
            raise RuntimeError("Signal handler called with signal %s" % signum)

        orig_sigalrm_handler = signal.getsignal(signal.SIGALRM)

        try:
            # set the signal handler and a 3-second alarm
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(3)

            # command to kill parent shell
            cmd = "kill -9 $$"

            workdir = os.path.realpath(self.test_prefix)
            change_dir(workdir)

            with self.mocked_stdout_stderr() as (_, stderr):
                self.assertErrorRegex(SystemExit, '.*', run_shell_cmd, cmd)

            # check error reporting output
            stderr = stderr.getvalue()
            patterns = [
                r"^\| full shell command[ ]*\| kill -9 \$\$",
                r"^\| exit code[ ]*\| -9",
                r"^\| working directory[ ]*\| " + workdir,
                r"^\| called from[ ]*\| assertErrorRegex function in .*/easybuild/base/testing.py \(line [0-9]+\)",
                r"^ERROR: shell command 'kill' failed!",
                r"^\| output \(stdout \+ stderr\)[ ]*\| .*/shell-cmd-error-.*/kill.out",
            ]
            for pattern in patterns:
                regex = re.compile(pattern, re.M)
                self.assertTrue(regex.search(stderr), "Pattern '%s' should be found in: %s" % (regex.pattern, stderr))

            # check error reporting output when stdout/stderr are collected separately
            with self.mocked_stdout_stderr() as (_, stderr):
                self.assertErrorRegex(SystemExit, '.*', run_shell_cmd, cmd, split_stderr=True)
            stderr = stderr.getvalue()
            patterns.pop(-1)
            patterns.extend([
                r"^\| output \(stdout\)[ ]*\| .*/shell-cmd-error-.*/kill.out",
                r"^\| error/warnings \(stderr\)[ ]*\| .*/shell-cmd-error-.*/kill.err",
            ])
            for pattern in patterns:
                regex = re.compile(pattern, re.M)
                self.assertTrue(regex.search(stderr), "Pattern '%s' should be found in: %s" % (regex.pattern, stderr))

            # no error reporting when fail_on_error is disabled
            with self.mocked_stdout_stderr() as (_, stderr):
                res = run_shell_cmd(cmd, fail_on_error=False)
            self.assertEqual(res.exit_code, -9)
            self.assertEqual(stderr.getvalue(), '')

        finally:
            # cleanup: disable the alarm + reset signal handler for SIGALRM
            signal.signal(signal.SIGALRM, orig_sigalrm_handler)
            signal.alarm(0)

    def test_run_cmd_bis(self):
        """More 'complex' test for run_cmd function."""
        # a more 'complex' command to run, make sure all required output is there
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd("for j in `seq 1 3`; do for i in `seq 1 100`; do echo hello; done; sleep 1.4; done")
        self.assertTrue(out.startswith('hello\nhello\n'))
        self.assertEqual(len(out), len("hello\n" * 300))
        self.assertEqual(ec, 0)

    def test_run_shell_cmd_bis(self):
        """More 'complex' test for run_shell_cmd function."""
        # a more 'complex' command to run, make sure all required output is there
        with self.mocked_stdout_stderr():
            res = run_shell_cmd("for j in `seq 1 3`; do for i in `seq 1 100`; do echo hello; done; sleep 1.4; done")
        self.assertTrue(res.output.startswith('hello\nhello\n'))
        self.assertEqual(len(res.output), len("hello\n" * 300))
        self.assertEqual(res.exit_code, 0)

    def test_run_cmd_work_dir(self):
        """
        Test running command in specific directory with run_cmd function.
        """
        orig_wd = os.getcwd()
        self.assertFalse(os.path.samefile(orig_wd, self.test_prefix))

        test_dir = os.path.join(self.test_prefix, 'test')
        for fn in ('foo.txt', 'bar.txt'):
            write_file(os.path.join(test_dir, fn), 'test')

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd("ls | sort", path=test_dir)

        self.assertEqual(ec, 0)
        self.assertEqual(out, 'bar.txt\nfoo.txt\n')

        self.assertTrue(os.path.samefile(orig_wd, os.getcwd()))

    def test_run_shell_cmd_work_dir(self):
        """
        Test running command in specific directory with run_shell_cmd function.
        """
        orig_wd = os.getcwd()
        self.assertFalse(os.path.samefile(orig_wd, self.test_prefix))

        test_dir = os.path.join(self.test_prefix, 'test')
        for fn in ('foo.txt', 'bar.txt'):
            write_file(os.path.join(test_dir, fn), 'test')

        cmd = "ls | sort"
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd, work_dir=test_dir)

        self.assertEqual(res.cmd, cmd)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, 'bar.txt\nfoo.txt\n')
        self.assertEqual(res.stderr, None)
        self.assertEqual(res.work_dir, test_dir)

        self.assertTrue(os.path.samefile(orig_wd, os.getcwd()))

    def test_run_cmd_log_output(self):
        """Test run_cmd with log_output enabled"""
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd("seq 1 100", log_output=True)
        self.assertEqual(ec, 0)
        self.assertEqual(type(out), str)
        self.assertTrue(out.startswith("1\n2\n"))
        self.assertTrue(out.endswith("99\n100\n"))

        run_cmd_logs = glob.glob(os.path.join(self.test_prefix, '*', 'easybuild-run_cmd*.log'))
        self.assertEqual(len(run_cmd_logs), 1)
        run_cmd_log_txt = read_file(run_cmd_logs[0])
        self.assertTrue(run_cmd_log_txt.startswith("# output for command: seq 1 100\n\n"))
        run_cmd_log_lines = run_cmd_log_txt.split('\n')
        self.assertEqual(run_cmd_log_lines[2:5], ['1', '2', '3'])
        self.assertEqual(run_cmd_log_lines[-4:-1], ['98', '99', '100'])

        # test running command that emits non-UTF-8 characters
        # this is constructed to reproduce errors like:
        # UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe2
        # UnicodeEncodeError: 'ascii' codec can't encode character u'\u2018' (‘)
        for text in [b"foo \xe2 bar", "foo ‘ bar"]:
            test_file = os.path.join(self.test_prefix, 'foo.txt')
            write_file(test_file, text)
            cmd = "cat %s" % test_file

            with self.mocked_stdout_stderr():
                (out, ec) = run_cmd(cmd, log_output=True)
            self.assertEqual(ec, 0)
            self.assertTrue(out.startswith('foo ') and out.endswith(' bar'))
            self.assertEqual(type(out), str)

    def test_run_shell_cmd_split_stderr(self):
        """Test getting split stdout/stderr output from run_shell_cmd function."""
        cmd = ';'.join([
            "echo ok",
            "echo warning >&2",
        ])

        # by default, output contains both stdout + stderr
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd)
        self.assertEqual(res.exit_code, 0)
        output_lines = res.output.split('\n')
        self.assertTrue("ok" in output_lines)
        self.assertTrue("warning" in output_lines)
        self.assertEqual(res.stderr, None)

        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd, split_stderr=True)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.stderr, "warning\n")
        self.assertEqual(res.output, "ok\n")

    def test_run_cmd_trace(self):
        """Test run_cmd in trace mode, and with tracing disabled."""

        pattern = [
            r"^  >> running command:",
            r"\t\[started at: .*\]",
            r"\t\[working dir: .*\]",
            r"\t\[output logged in .*\]",
            r"\techo hello",
            r"  >> command completed: exit 0, ran in .*",
        ]

        # trace output is enabled by default (since EasyBuild v5.0)
        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd("echo hello")
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(out, 'hello\n')
        self.assertEqual(ec, 0)
        self.assertEqual(stderr, '')
        regex = re.compile('\n'.join(pattern))
        self.assertTrue(regex.search(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))

        init_config(build_options={'trace': False})

        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd("echo hello")
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(out, 'hello\n')
        self.assertEqual(ec, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(stdout, '')

        init_config(build_options={'trace': True})

        # also test with command that is fed input via stdin
        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd('cat', inp='hello')
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(out, 'hello')
        self.assertEqual(ec, 0)
        self.assertEqual(stderr, '')
        pattern.insert(3, r"\t\[input: hello\]")
        pattern[-2] = "\tcat"
        regex = re.compile('\n'.join(pattern))
        self.assertTrue(regex.search(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))

        init_config(build_options={'trace': False})

        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd('cat', inp='hello')
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(out, 'hello')
        self.assertEqual(ec, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(stdout, '')

        # trace output can be disabled on a per-command basis
        for trace in (True, False):
            init_config(build_options={'trace': trace})

            self.mock_stdout(True)
            self.mock_stderr(True)
            (out, ec) = run_cmd("echo hello", trace=False)
            stdout = self.get_stdout()
            stderr = self.get_stderr()
            self.mock_stdout(False)
            self.mock_stderr(False)
            self.assertEqual(out, 'hello\n')
            self.assertEqual(ec, 0)
            self.assertEqual(stdout, '')
            self.assertEqual(stderr, '')

    def test_run_shell_cmd_trace(self):
        """Test run_shell_cmd function in trace mode, and with tracing disabled."""

        pattern = [
            r"^  >> running command:",
            r"\t\[started at: .*\]",
            r"\t\[working dir: .*\]",
            r"\t\[output logged in .*\]",
            r"\techo hello",
            r"  >> command completed: exit 0, ran in .*",
        ]

        # trace output is enabled by default (since EasyBuild v5.0)
        self.mock_stdout(True)
        self.mock_stderr(True)
        res = run_shell_cmd("echo hello")
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(stderr, '')
        regex = re.compile('\n'.join(pattern))
        self.assertTrue(regex.search(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))

        init_config(build_options={'trace': False})

        self.mock_stdout(True)
        self.mock_stderr(True)
        res = run_shell_cmd("echo hello")
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(stderr, '')
        self.assertEqual(stdout, '')

        init_config(build_options={'trace': True})

        # trace output can be disabled on a per-command basis via 'hidden' option
        for trace in (True, False):
            init_config(build_options={'trace': trace})

            self.mock_stdout(True)
            self.mock_stderr(True)
            res = run_shell_cmd("echo hello", hidden=True)
            stdout = self.get_stdout()
            stderr = self.get_stderr()
            self.mock_stdout(False)
            self.mock_stderr(False)
            self.assertEqual(res.output, 'hello\n')
            self.assertEqual(res.exit_code, 0)
            self.assertEqual(stdout, '')
            self.assertEqual(stderr, '')

    def test_run_shell_cmd_trace_stdin(self):
        """Test run_shell_cmd function under --trace + passing stdin input."""

        init_config(build_options={'trace': True})

        pattern = [
            r"^  >> running command:",
            r"\t\[started at: [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]\]",
            r"\t\[working dir: .*\]",
            r"\t\[output logged in .*\]",
            r"\techo hello",
            r"  >> command completed: exit 0, ran in .*",
        ]

        self.mock_stdout(True)
        self.mock_stderr(True)
        res = run_shell_cmd("echo hello")
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(stderr, '')
        regex = re.compile('\n'.join(pattern))
        self.assertTrue(regex.search(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))

        # also test with command that is fed input via stdin
        self.mock_stdout(True)
        self.mock_stderr(True)
        res = run_shell_cmd('cat', stdin='hello')
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(res.output, 'hello')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(stderr, '')
        pattern.insert(3, r"\t\[input: hello\]")
        pattern[-2] = "\tcat"
        regex = re.compile('\n'.join(pattern))
        self.assertTrue(regex.search(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))

        # trace output can be disabled on a per-command basis by enabling 'hidden'
        self.mock_stdout(True)
        self.mock_stderr(True)
        res = run_shell_cmd("echo hello", hidden=True)
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(res.output, 'hello\n')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')

    def test_run_cmd_qa(self):
        """Basic test for run_cmd_qa function."""

        cmd = "echo question; read x; echo $x"
        qa = {'question': 'answer'}
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, qa)
        self.assertEqual(out, "question\nanswer\n")
        # no reason echo hello could fail
        self.assertEqual(ec, 0)

        # test running command that emits non-UTF8 characters
        # this is constructed to reproduce errors like:
        # UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe2
        test_file = os.path.join(self.test_prefix, 'foo.txt')
        write_file(test_file, b"foo \xe2 bar")
        cmd += "; cat %s" % test_file

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, qa)
        self.assertEqual(ec, 0)
        self.assertTrue(out.startswith("question\nanswer\nfoo "))
        self.assertTrue(out.endswith('bar'))

    def test_run_cmd_qa_buffering(self):
        """Test whether run_cmd_qa uses unbuffered output."""

        # command that generates a lot of output before waiting for input
        # note: bug being fixed can be reproduced reliably using 1000, but not with too high values like 100000!
        cmd = 'for x in $(seq 1000); do echo "This is a number you can pick: $x"; done; '
        cmd += 'echo "Pick a number: "; read number; echo "Picked number: $number"'
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, {'Pick a number: ': '42'}, log_all=True, maxhits=5)

        self.assertEqual(ec, 0)
        regex = re.compile("Picked number: 42$")
        self.assertTrue(regex.search(out), "Pattern '%s' found in: %s" % (regex.pattern, out))

        # also test with script run as interactive command that quickly exits with non-zero exit code;
        # see https://github.com/easybuilders/easybuild-framework/issues/3593
        script_txt = '\n'.join([
            "#/bin/bash",
            "echo 'Hello, I am about to exit'",
            "echo 'ERROR: I failed' >&2",
            "exit 1",
        ])
        script = os.path.join(self.test_prefix, 'test.sh')
        write_file(script, script_txt)
        adjust_permissions(script, stat.S_IXUSR)

        with self.mocked_stdout_stderr():
            out, ec = run_cmd_qa(script, {}, log_ok=False)

        self.assertEqual(ec, 1)
        self.assertEqual(out, "Hello, I am about to exit\nERROR: I failed\n")

    def test_run_cmd_qa_log_all(self):
        """Test run_cmd_qa with log_output enabled"""
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa("echo 'n: '; read n; seq 1 $n", {'n: ': '5'}, log_all=True)
        self.assertEqual(ec, 0)
        self.assertEqual(out, "n: \n1\n2\n3\n4\n5\n")

        run_cmd_logs = glob.glob(os.path.join(self.test_prefix, '*', 'easybuild-run_cmd_qa*.log'))
        self.assertEqual(len(run_cmd_logs), 1)
        run_cmd_log_txt = read_file(run_cmd_logs[0])
        extra_pref = "# output for interactive command: echo 'n: '; read n; seq 1 $n\n\n"
        self.assertEqual(run_cmd_log_txt, extra_pref + "n: \n1\n2\n3\n4\n5\n")

    def test_run_cmd_qa_trace(self):
        """Test run_cmd under --trace"""
        # replace log.experimental with log.warning to allow experimental code
        easybuild.tools.utilities._log.experimental = easybuild.tools.utilities._log.warning

        init_config(build_options={'trace': True})

        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd_qa("echo 'n: '; read n; seq 1 $n", {'n: ': '5'})
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(stderr, '')
        pattern = r"^  >> running interactive command:\n"
        pattern += r"\t\[started at: .*\]\n"
        pattern += r"\t\[working dir: .*\]\n"
        pattern += r"\t\[output logged in .*\]\n"
        pattern += r"\techo \'n: \'; read n; seq 1 \$n\n"
        pattern += r'  >> interactive command completed: exit 0, ran in .*'
        self.assertTrue(re.search(pattern, stdout), "Pattern '%s' found in: %s" % (pattern, stdout))

        # trace output can be disabled on a per-command basis
        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd("echo hello", trace=False)
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')

    def test_run_cmd_qa_answers(self):
        """Test providing list of answers in run_cmd_qa."""
        cmd = "echo question; read x; echo $x; " * 2
        qa = {"question": ["answer1", "answer2"]}

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, qa)
        self.assertEqual(out, "question\nanswer1\nquestion\nanswer2\n")
        self.assertEqual(ec, 0)

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, {}, std_qa=qa)
        self.assertEqual(out, "question\nanswer1\nquestion\nanswer2\n")
        self.assertEqual(ec, 0)

        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, "Invalid type for answer", run_cmd_qa, cmd, {'q': 1})

        # test cycling of answers
        cmd = cmd * 2
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd_qa(cmd, {}, std_qa=qa)
        self.assertEqual(out, "question\nanswer1\nquestion\nanswer2\n" * 2)
        self.assertEqual(ec, 0)

    def test_run_cmd_simple(self):
        """Test return value for run_cmd in 'simple' mode."""
        with self.mocked_stdout_stderr():
            self.assertEqual(True, run_cmd("echo hello", simple=True))
            self.assertEqual(False, run_cmd("exit 1", simple=True, log_all=False, log_ok=False))

    def test_run_cmd_cache(self):
        """Test caching for run_cmd"""
        with self.mocked_stdout_stderr():
            (first_out, ec) = run_cmd("ulimit -u")
        self.assertEqual(ec, 0)
        with self.mocked_stdout_stderr():
            (cached_out, ec) = run_cmd("ulimit -u")
        self.assertEqual(ec, 0)
        self.assertEqual(first_out, cached_out)

        # inject value into cache to check whether executing command again really returns cached value
        with self.mocked_stdout_stderr():
            run_cmd.update_cache({("ulimit -u", None): ("123456", 123)})
            (cached_out, ec) = run_cmd("ulimit -u")
        self.assertEqual(ec, 123)
        self.assertEqual(cached_out, "123456")

        # also test with command that uses stdin
        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd("cat", inp='foo')
        self.assertEqual(ec, 0)
        self.assertEqual(out, 'foo')

        # inject different output for cat with 'foo' as stdin to check whether cached value is used
        with self.mocked_stdout_stderr():
            run_cmd.update_cache({('cat', 'foo'): ('bar', 123)})
            (cached_out, ec) = run_cmd("cat", inp='foo')
        self.assertEqual(ec, 123)
        self.assertEqual(cached_out, 'bar')

        run_cmd.clear_cache()

    def test_run_shell_cmd_cache(self):
        """Test caching for run_shell_cmd function"""

        cmd = "ulimit -u"
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd)
            first_out = res.output
        self.assertEqual(res.exit_code, 0)

        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd)
            cached_out = res.output
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(first_out, cached_out)

        # inject value into cache to check whether executing command again really returns cached value
        with self.mocked_stdout_stderr():
            cached_res = RunShellCmdResult(cmd=cmd, output="123456", exit_code=123, stderr=None,
                                           work_dir='/test_ulimit')
            run_shell_cmd.update_cache({(cmd, None): cached_res})
            res = run_shell_cmd(cmd)
        self.assertEqual(res.cmd, cmd)
        self.assertEqual(res.exit_code, 123)
        self.assertEqual(res.output, "123456")
        self.assertEqual(res.stderr, None)
        self.assertEqual(res.work_dir, '/test_ulimit')

        # also test with command that uses stdin
        cmd = "cat"
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(cmd, stdin='foo')
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, 'foo')

        # inject different output for cat with 'foo' as stdin to check whether cached value is used
        with self.mocked_stdout_stderr():
            cached_res = RunShellCmdResult(cmd=cmd, output="bar", exit_code=123, stderr=None, work_dir='/test_cat')
            run_shell_cmd.update_cache({(cmd, 'foo'): cached_res})
            res = run_shell_cmd(cmd, stdin='foo')
        self.assertEqual(res.cmd, cmd)
        self.assertEqual(res.exit_code, 123)
        self.assertEqual(res.output, 'bar')
        self.assertEqual(res.stderr, None)
        self.assertEqual(res.work_dir, '/test_cat')

        run_shell_cmd.clear_cache()

    def test_parse_log_error(self):
        """Test basic parse_log_for_error functionality."""
        errors = parse_log_for_error("error failed", True)
        self.assertEqual(len(errors), 1)

    def test_run_cmd_dry_run(self):
        """Test use of run_cmd function under (extended) dry run."""
        build_options = {
            'extended_dry_run': True,
            'silent': False,
        }
        init_config(build_options=build_options)

        cmd = "somecommand foo 123 bar"

        self.mock_stdout(True)
        run_cmd(cmd)
        stdout = self.get_stdout()
        self.mock_stdout(False)

        expected = """  running command "somecommand foo 123 bar"\n"""
        self.assertIn(expected, stdout)

        # check disabling 'verbose'
        self.mock_stdout(True)
        run_cmd("somecommand foo 123 bar", verbose=False)
        stdout = self.get_stdout()
        self.mock_stdout(False)
        self.assertNotIn(expected, stdout)

        # check forced run_cmd
        outfile = os.path.join(self.test_prefix, 'cmd.out')
        self.assertNotExists(outfile)
        self.mock_stdout(True)
        run_cmd("echo 'This is always echoed' > %s" % outfile, force_in_dry_run=True)
        self.mock_stdout(False)
        self.assertExists(outfile)
        self.assertEqual(read_file(outfile), "This is always echoed\n")

        # Q&A commands
        self.mock_stdout(True)
        run_cmd_qa("some_qa_cmd", {'question1': 'answer1'})
        stdout = self.get_stdout()
        self.mock_stdout(False)

        expected = """  running interactive command "some_qa_cmd"\n"""
        self.assertIn(expected, stdout)

    def test_run_shell_cmd_dry_run(self):
        """Test use of run_shell_cmd function under (extended) dry run."""
        build_options = {
            'extended_dry_run': True,
            'silent': False,
        }
        init_config(build_options=build_options)

        cmd = "somecommand foo 123 bar"

        self.mock_stdout(True)
        res = run_shell_cmd(cmd)
        stdout = self.get_stdout()
        self.mock_stdout(False)
        # fake output/exit code is returned for commands not actually run in dry run mode
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, '')
        self.assertEqual(res.stderr, None)
        # check dry run output
        expected = """  running command "somecommand foo 123 bar"\n"""
        self.assertIn(expected, stdout)

        # check enabling 'hidden'
        self.mock_stdout(True)
        res = run_shell_cmd(cmd, hidden=True)
        stdout = self.get_stdout()
        self.mock_stdout(False)
        # fake output/exit code is returned for commands not actually run in dry run mode
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, '')
        self.assertEqual(res.stderr, None)
        # dry run output should be missing
        self.assertNotIn(expected, stdout)

        # check forced run_cmd
        outfile = os.path.join(self.test_prefix, 'cmd.out')
        self.assertNotExists(outfile)
        self.mock_stdout(True)
        res = run_shell_cmd("echo 'This is always echoed' > %s; echo done; false" % outfile,
                            fail_on_error=False, in_dry_run=True)
        stdout = self.get_stdout()
        self.mock_stdout(False)
        self.assertNotIn('running command "', stdout)
        self.assertNotEqual(res.exit_code, 0)
        self.assertEqual(res.output, 'done\n')
        self.assertEqual(res.stderr, None)
        self.assertExists(outfile)
        self.assertEqual(read_file(outfile), "This is always echoed\n")

    def test_run_cmd_list(self):
        """Test run_cmd with command specified as a list rather than a string"""
        cmd = ['/bin/sh', '-c', "echo hello"]
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, "When passing cmd as a list then `shell` must be set explictely!",
                                  run_cmd, cmd)
            (out, ec) = run_cmd(cmd, shell=False)
        self.assertEqual(out, "hello\n")
        # no reason echo hello could fail
        self.assertEqual(ec, 0)

    def test_run_cmd_script(self):
        """Testing use of run_cmd with shell=False to call external scripts"""
        py_test_script = os.path.join(self.test_prefix, 'test.py')
        write_file(py_test_script, '\n'.join([
            '#!%s' % sys.executable,
            'print("hello")',
        ]))
        adjust_permissions(py_test_script, stat.S_IXUSR)

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd(py_test_script)
        self.assertEqual(ec, 0)
        self.assertEqual(out, "hello\n")

        with self.mocked_stdout_stderr():
            (out, ec) = run_cmd([py_test_script], shell=False)
        self.assertEqual(ec, 0)
        self.assertEqual(out, "hello\n")

    def test_run_cmd_stream(self):
        """Test use of run_cmd with streaming output."""
        self.mock_stdout(True)
        self.mock_stderr(True)
        (out, ec) = run_cmd("echo hello", stream_output=True)
        stdout = self.get_stdout()
        stderr = self.get_stderr()
        self.mock_stdout(False)
        self.mock_stderr(False)

        self.assertEqual(ec, 0)
        self.assertEqual(out, "hello\n")

        self.assertEqual(stderr, '')
        expected = [
            "== (streaming) output for command 'echo hello':",
            "hello",
            '',
        ]
        for line in expected:
            self.assertIn(line, stdout)

    def test_run_cmd_async(self):
        """Test asynchronously running of a shell command via run_cmd + complete_cmd."""

        os.environ['TEST'] = 'test123'

        test_cmd = "echo 'sleeping...'; sleep 2; echo $TEST"
        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(test_cmd, asynchronous=True)
        proc = cmd_info[0]

        # change value of $TEST to check that command is completed with correct environment
        os.environ['TEST'] = 'some_other_value'

        # initial poll should result in None, since it takes a while for the command to complete
        ec = proc.poll()
        self.assertEqual(ec, None)

        # wait until command is done
        while ec is None:
            time.sleep(1)
            ec = proc.poll()

        with self.mocked_stdout_stderr():
            out, ec = complete_cmd(*cmd_info, simple=False)
        self.assertEqual(ec, 0)
        self.assertEqual(out, 'sleeping...\ntest123\n')

        # also test use of check_async_cmd function
        os.environ['TEST'] = 'test123'
        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(test_cmd, asynchronous=True)

        # first check, only read first 12 output characters
        # (otherwise we'll be waiting until command is completed)
        res = check_async_cmd(*cmd_info, output_read_size=12)
        self.assertEqual(res, {'done': False, 'exit_code': None, 'output': 'sleeping...\n'})

        # 2nd check with default output size (1024) gets full output
        # (keep checking until command is fully done)
        while not res['done']:
            res = check_async_cmd(*cmd_info, output=res['output'])
        self.assertEqual(res, {'done': True, 'exit_code': 0, 'output': 'sleeping...\ntest123\n'})

        # check asynchronous running of failing command
        error_test_cmd = "echo 'FAIL!' >&2; exit 123"
        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(error_test_cmd, asynchronous=True)
        time.sleep(1)
        error_pattern = 'cmd ".*" exited with exit code 123'
        self.assertErrorRegex(EasyBuildError, error_pattern, check_async_cmd, *cmd_info)

        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(error_test_cmd, asynchronous=True)
        res = check_async_cmd(*cmd_info, fail_on_error=False)
        # keep checking until command is fully done
        while not res['done']:
            res = check_async_cmd(*cmd_info, fail_on_error=False, output=res['output'])
        self.assertEqual(res, {'done': True, 'exit_code': 123, 'output': "FAIL!\n"})

        # also test with a command that produces a lot of output,
        # since that tends to lock up things unless we frequently grab some output...
        verbose_test_cmd = ';'.join([
            "echo start",
            "for i in $(seq 1 50)",
            "do sleep 0.1",
            "for j in $(seq 1000)",
            "do echo foo",
            "done",
            "done",
            "echo done",
        ])
        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(verbose_test_cmd, asynchronous=True)
        proc = cmd_info[0]

        output = ''
        ec = proc.poll()
        self.assertEqual(ec, None)

        while ec is None:
            time.sleep(1)
            output += get_output_from_process(proc)
            ec = proc.poll()

        with self.mocked_stdout_stderr():
            out, ec = complete_cmd(*cmd_info, simple=False, output=output)
        self.assertEqual(ec, 0)
        self.assertTrue(out.startswith('start\n'))
        self.assertTrue(out.endswith('\ndone\n'))

        # also test use of check_async_cmd on verbose test command
        with self.mocked_stdout_stderr():
            cmd_info = run_cmd(verbose_test_cmd, asynchronous=True)

        error_pattern = r"Number of output bytes to read should be a positive integer value \(or zero\)"
        self.assertErrorRegex(EasyBuildError, error_pattern, check_async_cmd, *cmd_info, output_read_size=-1)
        self.assertErrorRegex(EasyBuildError, error_pattern, check_async_cmd, *cmd_info, output_read_size='foo')

        # with output_read_size set to 0, no output is read yet, only status of command is checked
        with self.mocked_stdout_stderr():
            res = check_async_cmd(*cmd_info, output_read_size=0)
        self.assertEqual(res['done'], False)
        self.assertEqual(res['exit_code'], None)
        self.assertEqual(res['output'], '')

        with self.mocked_stdout_stderr():
            res = check_async_cmd(*cmd_info)
        self.assertEqual(res['done'], False)
        self.assertEqual(res['exit_code'], None)
        self.assertTrue(res['output'].startswith('start\n'))
        self.assertFalse(res['output'].endswith('\ndone\n'))
        # keep checking until command is complete
        while not res['done']:
            res = check_async_cmd(*cmd_info, output=res['output'])
        self.assertEqual(res['done'], True)
        self.assertEqual(res['exit_code'], 0)
        self.assertTrue(res['output'].startswith('start\n'))
        self.assertTrue(res['output'].endswith('\ndone\n'))

    def test_check_log_for_errors(self):
        fd, logfile = tempfile.mkstemp(suffix='.log', prefix='eb-test-')
        os.close(fd)

        self.assertErrorRegex(EasyBuildError, "Invalid input:", check_log_for_errors, "", [42])
        self.assertErrorRegex(EasyBuildError, "Invalid input:", check_log_for_errors, "", [(42, IGNORE)])
        self.assertErrorRegex(EasyBuildError, "Invalid input:", check_log_for_errors, "", [("42", "invalid-mode")])
        self.assertErrorRegex(EasyBuildError, "Invalid input:", check_log_for_errors, "", [("42", IGNORE, "")])

        input_text = "\n".join([
            "OK",
            "error found",
            "test failed",
            "msg: allowed-test failed",
            "enabling -Werror",
            "the process crashed with 0"
        ])
        expected_msg = r"Found 2 error\(s\) in command output "\
                       r"\(output: error found\n\tthe process crashed with 0\)"

        # String promoted to list
        self.assertErrorRegex(EasyBuildError, expected_msg, check_log_for_errors, input_text,
                              r"\b(error|crashed)\b")
        # List of string(s)
        self.assertErrorRegex(EasyBuildError, expected_msg, check_log_for_errors, input_text,
                              [r"\b(error|crashed)\b"])
        # List of tuple(s)
        self.assertErrorRegex(EasyBuildError, expected_msg, check_log_for_errors, input_text,
                              [(r"\b(error|crashed)\b", ERROR)])

        expected_msg = "Found 2 potential error(s) in command output " \
                       "(output: error found\n\tthe process crashed with 0)"
        init_logging(logfile, silent=True)
        check_log_for_errors(input_text, [(r"\b(error|crashed)\b", WARN)])
        stop_logging(logfile)
        self.assertIn(expected_msg, read_file(logfile))

        expected_msg = r"Found 2 error\(s\) in command output \(output: error found\n\ttest failed\)"
        write_file(logfile, '')
        init_logging(logfile, silent=True)
        self.assertErrorRegex(EasyBuildError, expected_msg, check_log_for_errors, input_text, [
            r"\berror\b",
            (r"\ballowed-test failed\b", IGNORE),
            (r"(?i)\bCRASHED\b", WARN),
            "fail"
        ])
        stop_logging(logfile)
        expected_msg = "Found 1 potential error(s) in command output (output: the process crashed with 0)"
        self.assertIn(expected_msg, read_file(logfile))

    def test_run_cmd_with_hooks(self):
        """
        Test running command with run_cmd with pre/post run_shell_cmd hooks in place.
        """
        cwd = os.getcwd()

        hooks_file = os.path.join(self.test_prefix, 'my_hooks.py')
        hooks_file_txt = textwrap.dedent("""
            def pre_run_shell_cmd_hook(cmd, *args, **kwargs):
                work_dir = kwargs['work_dir']
                if kwargs.get('interactive'):
                    print("pre-run hook interactive '%s' in %s" % (cmd, work_dir))
                else:
                    print("pre-run hook '%s' in %s" % (cmd, work_dir))
                if not cmd.startswith('echo'):
                    cmds = cmd.split(';')
                    return '; '.join(cmds[:-1] + ["echo " + cmds[-1].lstrip()])

            def post_run_shell_cmd_hook(cmd, *args, **kwargs):
                exit_code = kwargs.get('exit_code')
                output = kwargs.get('output')
                work_dir = kwargs['work_dir']
                if kwargs.get('interactive'):
                    msg = "post-run hook interactive '%s'" % cmd
                else:
                    msg = "post-run hook '%s'" % cmd
                msg += " (exit code: %s, output: '%s')" % (exit_code, output)
                print(msg)
        """)
        write_file(hooks_file, hooks_file_txt)
        update_build_option('hooks', hooks_file)

        # disable trace output to make checking of generated output produced by hooks easier
        update_build_option('trace', False)

        with self.mocked_stdout_stderr():
            run_cmd("make")
            stdout = self.get_stdout()

        expected_stdout = '\n'.join([
            "pre-run hook 'make' in %s" % cwd,
            "post-run hook 'echo make' (exit code: 0, output: 'make\n')",
            '',
        ])
        self.assertEqual(stdout, expected_stdout)

        with self.mocked_stdout_stderr():
            run_cmd_qa("sleep 2; make", qa={})
            stdout = self.get_stdout()

        expected_stdout = '\n'.join([
            "pre-run hook interactive 'sleep 2; make' in %s" % cwd,
            "post-run hook interactive 'sleep 2; echo make' (exit code: 0, output: 'make\n')",
            '',
        ])
        self.assertEqual(stdout, expected_stdout)

    def test_run_shell_cmd_with_hooks(self):
        """
        Test running command with run_shell_cmd function with pre/post run_shell_cmd hooks in place.
        """
        cwd = os.getcwd()

        hooks_file = os.path.join(self.test_prefix, 'my_hooks.py')
        hooks_file_txt = textwrap.dedent("""
            def pre_run_shell_cmd_hook(cmd, *args, **kwargs):
                work_dir = kwargs['work_dir']
                if kwargs.get('interactive'):
                    print("pre-run hook interactive '||%s||' in %s" % (cmd, work_dir))
                else:
                    print("pre-run hook '%s' in %s" % (cmd, work_dir))
                    import sys
                    sys.stderr.write('pre-run hook done\\n')
                if not cmd.startswith('echo'):
                    cmds = cmd.split(';')
                    return '; '.join(cmds[:-1] + ["echo " + cmds[-1].lstrip()])

            def post_run_shell_cmd_hook(cmd, *args, **kwargs):
                exit_code = kwargs.get('exit_code')
                output = kwargs.get('output')
                work_dir = kwargs['work_dir']
                if kwargs.get('interactive'):
                    msg = "post-run hook interactive '%s'" % cmd
                else:
                    msg = "post-run hook '%s'" % cmd
                msg += " (exit code: %s, output: '%s')" % (exit_code, output)
                print(msg)
        """)
        write_file(hooks_file, hooks_file_txt)
        update_build_option('hooks', hooks_file)

        # disable trace output to make checking of generated output produced by hooks easier
        update_build_option('trace', False)

        with self.mocked_stdout_stderr():
            run_shell_cmd("make")
            stdout = self.get_stdout()

        expected_stdout = '\n'.join([
            "pre-run hook 'make' in %s" % cwd,
            "post-run hook 'echo make' (exit code: 0, output: 'make\n')",
            '',
        ])
        self.assertEqual(stdout, expected_stdout)


def suite():
    """ returns all the testcases in this module """
    return TestLoaderFiltered().loadTestsFromTestCase(RunTest, sys.argv[1:])


if __name__ == '__main__':
    res = TextTestRunner(verbosity=1).run(suite())
    sys.exit(len(res.failures))
