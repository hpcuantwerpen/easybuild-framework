# #
# Copyright 2013-2025 Ghent University
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
Unit tests for eb command line options.

@author: Kenneth Hoste (Ghent University)
"""
import glob
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import textwrap
from importlib import reload
from unittest import TextTestRunner
from urllib.request import URLError

import easybuild.main
import easybuild.tools.build_log
import easybuild.tools.options
import easybuild.tools.toolchain
from easybuild.base import fancylogger
from easybuild.framework.easyblock import EasyBlock
from easybuild.framework.easyconfig import BUILD, CUSTOM, DEPENDENCIES, EXTENSIONS, FILEMANAGEMENT, LICENSE
from easybuild.framework.easyconfig import MANDATORY, MODULES, OTHER, TOOLCHAIN
from easybuild.framework.easyconfig.easyconfig import EasyConfig, get_easyblock_class, robot_find_easyconfig
from easybuild.framework.easyconfig.parser import EasyConfigParser
from easybuild.tools.build_log import EasyBuildError, EasyBuildLog
from easybuild.tools.config import DEFAULT_MODULECLASSES, BuildOptions, ConfigurationVariables
from easybuild.tools.config import build_option, find_last_log, get_build_log_path, get_module_syntax, module_classes
from easybuild.tools.environment import modify_env
from easybuild.tools.filetools import adjust_permissions, change_dir, copy_dir, copy_file, download_file
from easybuild.tools.filetools import is_patch_file, mkdir, move_file, parse_http_header_fields_urlpat
from easybuild.tools.filetools import read_file, remove_dir, remove_file, which, write_file
from easybuild.tools.github import GITHUB_RAW, GITHUB_EB_MAIN, GITHUB_EASYCONFIGS_REPO
from easybuild.tools.github import URL_SEPARATOR, fetch_github_token
from easybuild.tools.module_generator import ModuleGeneratorTcl
from easybuild.tools.modules import Lmod
from easybuild.tools.options import EasyBuildOptions, opts_dict_to_eb_opts, parse_external_modules_metadata
from easybuild.tools.options import set_up_configuration, set_tmpdir, use_color
from easybuild.tools.toolchain.utilities import TC_CONST_PREFIX
from easybuild.tools.run import run_shell_cmd
from easybuild.tools.systemtools import DARWIN, HAVE_ARCHSPEC, get_os_type
from easybuild.tools.version import VERSION
from test.framework.utilities import EnhancedTestCase, TestLoaderFiltered, cleanup, init_config

try:
    import pycodestyle  # noqa
except ImportError:
    pass


EXTERNAL_MODULES_METADATA = """[foobar/1.2.3]
name = foo, bar
version = 1.2.3, 3.2.1
prefix = FOOBAR_DIR

[foobar/2.0]
name = foobar
version = 2.0
prefix = FOOBAR_PREFIX

[foo]
name = Foo
prefix = /foo

[bar/1.2.3]
name = bar
version = 1.2.3
"""

# test account, for which a token may be available
GITHUB_TEST_ACCOUNT = 'easybuild_test'


class CommandLineOptionsTest(EnhancedTestCase):
    """Testcases for command line options."""

    logfile = None

    def setUp(self):
        """Set up test."""
        super().setUp()
        self.github_token = fetch_github_token(GITHUB_TEST_ACCOUNT)

        self.orig_terminal_supports_colors = easybuild.tools.options.terminal_supports_colors
        self.orig_os_getuid = easybuild.main.os.getuid
        self.orig_experimental = easybuild.tools.build_log.EXPERIMENTAL

    def tearDown(self):
        """Clean up after test."""
        easybuild.main.os.getuid = self.orig_os_getuid
        easybuild.tools.options.terminal_supports_colors = self.orig_terminal_supports_colors
        easybuild.tools.build_log.EXPERIMENTAL = self.orig_experimental

        super().tearDown()

    def purge_environment(self):
        """Remove any leftover easybuild variables"""
        for var in os.environ.keys():
            # retain $EASYBUILD_IGNORECONFIGFILES, to make sure the test is isolated from system-wide config files!
            if var.startswith('EASYBUILD_') and var != 'EASYBUILD_IGNORECONFIGFILES':
                del os.environ[var]

    def test_help_short(self, txt=None):
        """Test short help message."""

        if txt is None:
            topt = EasyBuildOptions(
                go_args=['-h'],
                go_nosystemexit=True,  # when printing help, optparse ends with sys.exit
                go_columns=100,  # fix col size for reproducible unittest output
                help_to_string=True,  # don't print to stdout, but to StingIO fh,
                prog='easybuildoptions_test',  # generate as if called from generaloption.py
            )

            outtxt = topt.parser.help_to_file.getvalue()
        else:
            outtxt = txt

        self.assertIn(' -h ', outtxt, "Only short options included in short help")
        self.assertIn("show short help message and exit", outtxt, "Documentation included in short help")
        self.assertNotIn("--short-help ", outtxt, "Long options not included in short help")
        self.assertNotIn("Software search and build options", outtxt,
                         "Not all option groups included in short help (1)")
        self.assertNotIn("Regression test options", outtxt,
                         "Not all option groups included in short help (2)")

    def test_help_long(self):
        """Test long help message."""

        topt = EasyBuildOptions(
            go_args=['-H'],
            go_nosystemexit=True,  # when printing help, optparse ends with sys.exit
            go_columns=200,  # fix col size for reproducible unittest output
            help_to_string=True,  # don't print to stdout, but to StingIO fh,
            prog='easybuildoptions_test',  # generate as if called from generaloption.py
        )
        outtxt = topt.parser.help_to_file.getvalue()

        self.assertIn("-H OUTPUT_FORMAT, --help=OUTPUT_FORMAT", outtxt,
                      "Long documentation expanded in long help")
        self.assertIn("show short help message and exit", outtxt,
                      "Documentation included in long help")
        self.assertIn("Software search and build options", outtxt,
                      "Not all option groups included in short help (1)")
        self.assertIn("Regression test options", outtxt,
                      "Not all option groups included in short help (2)")

        # for boolean options, we mention in the help text how to disable them
        regex = re.compile(r"default: True; disable with\s*--disable-\s*cleanup-\s*builddir", re.M)
        self.assertRegex(outtxt, regex)

    def test_help_rst(self):
        """Test generating --help in RST output format."""

        with self.mocked_stdout_stderr():
            self.eb_main(['--help=rst'], raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout()

        self.assertFalse(stderr)

        patterns = [
            r"^Basic options\n-------------",
            r"^``--fetch``[ ]*Allow downloading sources",
        ]
        self._assert_regexs(patterns, stdout)

    def test_no_args(self):
        """Test using no arguments."""

        with self.mocked_stdout_stderr():
            outtxt = self.eb_main([])

        error_msg = "ERROR.* Please provide one or multiple easyconfig files,"
        error_msg += " or use software build options to make EasyBuild search for easyconfigs"
        self.assertRegex(outtxt, error_msg)

    def test_debug(self):
        """Test enabling debug logging."""
        error_tmpl = "%s log messages are included when using %s: %s"
        for debug_arg in ['-d', '--debug']:
            args = [
                'nosuchfile.eb',
                debug_arg,
            ]
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args)

            for log_msg_type in ['DEBUG', 'INFO', 'ERROR']:
                res = re.search(' %s ' % log_msg_type, outtxt)
                self.assertTrue(res, error_tmpl % (log_msg_type, debug_arg, outtxt))

    def test_info(self):
        """Test enabling info logging."""

        for info_arg in ['--info']:
            args = [
                'nosuchfile.eb',
                info_arg,
            ]
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args)

            error_tmpl = "%s log messages are included when using %s ( out: %s)"
            for log_msg_type in ['INFO', 'ERROR']:
                res = re.search(' %s ' % log_msg_type, outtxt)
                self.assertTrue(res, error_tmpl % (log_msg_type, info_arg, outtxt))

            for log_msg_type in ['DEBUG']:
                res = re.search(' %s ' % log_msg_type, outtxt)
                self.assertTrue(not res, "%s log messages are *not* included when using %s" % (log_msg_type, info_arg))

    def test_quiet(self):
        """Test enabling quiet logging (errors only)."""
        for quiet_arg in ['--quiet']:
            args = ['nosuchfile.eb', quiet_arg]
            with self.mocked_stdout_stderr():
                out = self.eb_main(args)

            for log_msg_type in ['ERROR']:
                res = re.search(' %s ' % log_msg_type, out)
                msg = "%s log messages are included when using %s (out: %s)" % (log_msg_type, quiet_arg, out)
                self.assertTrue(res, msg)

            for log_msg_type in ['DEBUG', 'INFO']:
                res = re.search(' %s ' % log_msg_type, out)
                msg = "%s log messages are *not* included when using %s (out: %s)" % (log_msg_type, quiet_arg, out)
                self.assertTrue(not res, msg)

    def test_force(self):
        """Test forcing installation even if the module is already available."""

        # use GCC-4.6.3.eb easyconfig file that comes with the tests
        eb_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 'g', 'GCC', 'GCC-4.6.3.eb')

        # check log message without --force
        args = [
            eb_file,
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            outtxt, error_thrown = self.eb_main(args, return_error=True)

        error_msg = "No error is thrown if software is already installed (error_thrown: %s)" % error_thrown
        self.assertTrue(not error_thrown, error_msg)

        already_msg = "GCC/4.6.3 is already installed"
        error_msg = "Already installed message without --force, outtxt: %s" % outtxt
        self.assertIn(already_msg, outtxt, error_msg)

        # clear log file
        write_file(self.logfile, '')

        # check that --force and --rebuild work
        for arg in ['--force', '--rebuild']:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main([eb_file, '--debug', arg])
            self.assertNotIn(already_msg, outtxt, "Already installed message not there with %s" % arg)

    def test_skip(self):
        """Test skipping installation of module (--skip, -k)."""
        # use toy-0.0.eb easyconfig file that comes with the tests
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # check log message with --skip for existing module
        args = [
            toy_ec,
            '--force',
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True)

        args.append('--skip')
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True)

        found_msg = "Module toy/0.0 found.\n[^\n]+Going to skip actual main build"
        found = re.search(found_msg, outtxt, re.M)
        self.assertTrue(found, "Module found message present with --skip, outtxt: %s" % outtxt)

        # cleanup for next test
        write_file(self.logfile, '')
        os.chdir(self.cwd)

        # check log message with --skip for non-existing module
        args = [
            toy_ec,
            '--try-software-version=1.2.3.4.5.6.7.8.9',
            '--try-amend=sources=toy-0.0.tar.gz,toy-0.0.tar.gz',  # hackish, but fine
            '--force',
            '--debug',
            '--skip',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True)

        self.assertNotIn("Module toy/1.2.3.4.5.6.7.8.9 found.", outtxt,
                         "Module found message should not be there with --skip for non-existing modules")

        self.assertIn("No module toy/1.2.3.4.5.6.7.8.9 found. Not skipping anything.", outtxt,
                      "Module not found message should be there with --skip for non-existing modules")

        toy_mod_glob = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '*')
        for toy_mod in glob.glob(toy_mod_glob):
            remove_file(toy_mod)
        self.assertFalse(glob.glob(toy_mod_glob))

        # make sure that sanity check is *NOT* skipped under --skip
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        test_ec_txt = read_file(toy_ec)
        regex = re.compile(r"sanity_check_paths = \{(.|\n)*\}", re.M)
        test_ec_txt = regex.sub("sanity_check_paths = {'files': ['bin/nosuchfile'], 'dirs': []}", test_ec_txt)
        write_file(test_ec, test_ec_txt)
        args = [
            test_ec,
            '--skip',
            '--force',
        ]
        error_pattern = "Sanity check failed: no file found at 'bin/nosuchfile'"
        self.assertErrorRegex(EasyBuildError, error_pattern, self.mocked_main, args, do_build=True, raise_error=True)

    def test_module_only_param(self):
        """check use of module_only parameter"""
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        test_ec = os.path.join(self.test_prefix, 'test.eb')
        test_ec_txt = read_file(toy_ec)
        test_ec_txt += "\nmodule_only=True\n"
        test_ec_txt += "\nskipsteps = ['sanitycheck']\n"  # Software does not exist, so sanity check would fail
        write_file(test_ec, test_ec_txt)

        args = [
            test_ec,
            '--rebuild',
        ]
        self.mocked_main(args, do_build=True, raise_error=True)

        toy_mod_glob = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '*')
        self.assertEqual(len(glob.glob(toy_mod_glob)), 1)

        # check that no software was installed
        installdir = os.path.join(self.test_installpath, 'software', 'toy', '0.0')
        installdir_glob = glob.glob(os.path.join(installdir, '*'))
        easybuild_dir = os.path.join(installdir, 'easybuild')
        self.assertEqual(installdir_glob, [easybuild_dir])

    def test_skipsteps(self):
        """Test skipping of steps using skipsteps."""
        # use toy-0.0.eb easyconfig file that comes with the tests
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # make sure that sanity check is *NOT* skipped
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        test_ec_txt = read_file(toy_ec)
        regex = re.compile(r"sanity_check_paths = \{(.|\n)*\}", re.M)
        test_ec_txt = regex.sub("sanity_check_paths = {'files': ['bin/nosuchfile'], 'dirs': []}", test_ec_txt)
        write_file(test_ec, test_ec_txt)
        args = [
            test_ec,
            '--rebuild',
        ]
        error_pattern = "Sanity check failed: no file found at 'bin/nosuchfile'"
        self.assertErrorRegex(EasyBuildError, error_pattern, self.mocked_main, args, do_build=True, raise_error=True)

        # Verify a wrong step name is caught
        test_ec_txt += "\nskipsteps = ['wrong-step-name']\n"
        write_file(test_ec, test_ec_txt)
        error_pattern = "Found one or more unknown step names in 'skipsteps' easyconfig parameter:\n"
        error_pattern += r"\* wrong-step-name"
        self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)
        # 'source' step was renamed to 'extract' in EasyBuild 5.0,
        # see https://github.com/easybuilders/easybuild-framework/pull/4629
        test_ec_txt += "\nskipsteps = ['source']\n"
        write_file(test_ec, test_ec_txt)
        error_pattern = error_pattern.replace('wrong-step-name', r"source \(did you mean 'extract'\?\)")
        self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

        # check use of skipsteps to skip sanity check
        test_ec_txt += "\nskipsteps = ['sanitycheck']\n"
        write_file(test_ec, test_ec_txt)
        self.mocked_main(args, do_build=True, raise_error=True)

        toy_mod_glob = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '*')
        self.assertEqual(len(glob.glob(toy_mod_glob)), 1)

    def test_skip_test_step(self):
        """Test skipping testing the build (--skip-test-step)."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-test.eb')

        # check log message without --skip-test-step
        args = [
            toy_ec,
            '--extended-dry-run',
            '--force',
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True)
        found_msg = "Running method test_step part of step test"
        found = re.search(found_msg, outtxt)
        test_run_msg = "execute make_test dummy_cmd as a command for running unit tests"
        self.assertTrue(found, "Message about test step being run is present, outtxt: %s" % outtxt)
        found = re.search(test_run_msg, outtxt)
        self.assertTrue(found, "Test execution command is present, outtxt: %s" % outtxt)

        # And now with the argument
        args.append('--skip-test-step')
        with self.mocked_stdout_stderr() as (_, stderr):
            outtxt = self.eb_main(args, do_build=True)
        found_msg = "Skipping test step"
        found = re.search(found_msg, outtxt)
        self.assertTrue(found, "Message about test step being skipped is present, outtxt: %s" % outtxt)
        # Warning should be printed to stderr
        self.assertIn('Will not run the test step as requested via skip-test-step', stderr.getvalue())
        found = re.search(test_run_msg, outtxt)
        self.assertFalse(found, "Test execution command is NOT present, outtxt: %s" % outtxt)

    def test_ignore_test_failure(self):
        """Test ignore failing tests (--ignore-test-failure)."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        # This EC uses a `runtest` command which does not exist and hence will make the test step fail
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-test.eb')

        args = [toy_ec, '--ignore-test-failure', '--force']

        with self.mocked_stdout_stderr() as (_, stderr):
            outtxt = self.eb_main(args, do_build=True)

        msg = 'Test failure ignored'
        self.assertIn(msg, outtxt,
                      "Ignored test failure message in log should be found, outtxt: %s" % outtxt)
        self.assertIn(msg, stderr.getvalue(),
                      "Ignored test failure message in stderr should be found, stderr: %s" % stderr.getvalue())

        # Passing skip and ignore options is disallowed
        args.append('--skip-test-step')
        error_pattern = 'Found both ignore-test-failure and skip-test-step enabled'
        self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

    def test_skip_sanity_check(self):
        """Test skipping of sanity check step (--skip-sanity-check)."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        write_file(test_ec, read_file(toy_ec) + "\nsanity_check_commands = ['this_will_fail']")

        args = [test_ec, '--rebuild']
        err_msg = "Sanity check failed"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, err_msg, self.eb_main, args, do_build=True, raise_error=True)

        args.append('--skip-sanity-check')
        with self.mocked_stdout_stderr():
            outtext = self.eb_main(args, do_build=True, raise_error=True)
        self.assertNotIn('sanity checking...', outtext)

        # Passing skip and only options is disallowed
        args.append('--sanity-check-only')
        error_pattern = 'Found both skip-sanity-check and sanity-check-only enabled'
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

    def test_job(self):
        """Test submitting build as a job."""

        # use gzip-1.4.eb easyconfig file that comes with the tests
        test_ecs = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs')
        eb_file = os.path.join(test_ecs, 'g', 'gzip', 'gzip-1.4.eb')

        def check_args(job_args, passed_args=None, msgstrs=None, try_opts='', tweaked_eb_file='gzip-1.4.eb'):
            """Check whether specified args yield expected result."""
            if passed_args is None:
                passed_args = job_args[:]

            # clear log file
            write_file(self.logfile, '')

            args = [
                eb_file,
                '--job',
            ] + job_args
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, raise_error=True)

            job_msg = r"INFO.* Command template for jobs: .* && eb %%\(spec\)s.* %s.*\n" % ' .*'.join(passed_args)
            assertmsg = "Info log msg with job command template for --job (job_msg: %s, outtxt: %s)" % (job_msg, outtxt)
            self.assertRegex(outtxt, job_msg, assertmsg)

            if msgstrs is None:
                msgstrs = [(tweaked_eb_file, eb_file + try_opts)]

            assertmsg = "Info log msg with creating job for --job (job_msg: %s, outtxt: %s)" % (job_msg, outtxt)
            for msgstr in msgstrs:
                job_msg = "INFO creating job for ec: %s using %s\n" % msgstr
                self.assertIn(job_msg, outtxt, assertmsg)

        # options passed are reordered, so order here matters to make tests pass
        check_args(['--debug'])
        check_args(['--debug', '--stop=configure', '--try-software-name=foo'],
                   passed_args=['--debug', "--stop='configure'"],
                   try_opts=" --try-software-name='foo'",
                   tweaked_eb_file="foo-1.4.eb")
        check_args(['--debug', '--robot-paths=/tmp/foo:/tmp/bar'],
                   passed_args=['--debug', "--robot-paths='/tmp/foo:/tmp/bar'"])
        # --robot has preference over --robot-paths, --robot is not passed down
        check_args(['--debug', '--robot-paths=/tmp/foo', '--robot=%s' % self.test_prefix],
                   passed_args=['--debug', "--robot-paths='%s:/tmp/foo'" % self.test_prefix])

        # check if libtoy dep uses --try-toolchain but gzip does not (easyconfig exists already)
        eb_file = os.path.join(self.test_buildpath, 'toy-0.0-with-deps.eb')
        copy_file(os.path.join(test_ecs, 't', 'toy', 'toy-0.0.eb'), eb_file)
        write_file(eb_file, "dependencies = [('libtoy', '0.0'), ('gzip', '1.4')]\n", append=True)
        try_opts = " --try-toolchain='GCC,4.9.3-2.26'"
        tweaked_eb_file = "toy-0.0-GCC-4.9.3-2.26.eb"
        gzip_eb_file = 'gzip-1.4-GCC-4.9.3-2.26.eb'
        check_args(['--debug', '--stop=configure', '--try-toolchain=GCC,4.9.3-2.26', '--robot'],
                   passed_args=['--debug', "--stop='configure'"],
                   msgstrs=[
                       (tweaked_eb_file, eb_file + try_opts),
                       ('libtoy-0.0-GCC-4.9.3-2.26.eb',
                        os.path.join(test_ecs, 'l', 'libtoy', 'libtoy-0.0.eb') + try_opts),
                       (gzip_eb_file, os.path.join(test_ecs, 'g', 'gzip', gzip_eb_file))],
                   try_opts=try_opts,
                   tweaked_eb_file=tweaked_eb_file)

    # 'zzz' prefix in the test name is intentional to make this test run last,
    # since it fiddles with the logging infrastructure which may break things
    def test_zzz_logtostdout(self):
        """Testing redirecting log to stdout."""

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        for stdout_arg in ['--logtostdout', '-l']:

            args = [
                '--software-name=somethingrandom',
                '--robot', '.',
                '--debug',
                stdout_arg,
            ]
            with self.mocked_stdout_stderr(mock_stderr=False):
                self.eb_main(args, logfile=dummylogfn)
                stdout = self.get_stdout()

            # make sure we restore
            fancylogger.logToScreen(enable=False, stdout=True)

            error_msg = "Log messages are printed to stdout when %s is used (stdout: %s)" % (stdout_arg, stdout)
            self.assertTrue(len(stdout) > 100, error_msg)

        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ecfile = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        self.logfile = None

        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main([toy_ecfile, '--debug', '-l', '--force'], do_build=True, raise_error=True)
            stdout = self.get_stdout()

        self.assertIn("Auto-enabling streaming output", stdout)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_avail_easyconfig_constants(self):
        """Test listing available easyconfig file constants."""

        def run_test(fmt=None):
            """Helper function to test --avail-easyconfig-constants."""

            args = ['--avail-easyconfig-constants']
            if fmt is not None:
                args.append('--output-format=%s' % fmt)

            with self.mocked_stdout_stderr():
                self.eb_main(args, verbose=True, raise_error=True)
                stderr, stdout = self.get_stderr(), self.get_stdout()

            self.assertFalse(stderr)

            if fmt == 'rst':
                pattern_lines = [
                    r'^``ARCH``\s*``(aarch64|ppc64le|x86_64)``\s*CPU architecture .*',
                    r'^``EXTERNAL_MODULE``.*',
                    r'^``HOME``.*',
                    r'^``MODULE_LOAD_ENV_HEADERS``.*Environment variables .*',
                    r'``OS_NAME``.*',
                    r'``OS_PKG_IBVERBS_DEV``.*',
                ]
            else:
                pattern_lines = [
                    r'^\s*ARCH: (aarch64|ppc64le|x86_64) \(CPU architecture .*\)',
                    r'^\s*EXTERNAL_MODULE:.*',
                    r'^\s*HOME:.*',
                    r'^\s*MODULE_LOAD_ENV_HEADERS:.*\(Environment variables.*\)',
                    r'\s*OS_NAME: .*',
                    r'\s*OS_PKG_IBVERBS_DEV: .*',
                ]

            regex = re.compile('\n'.join(pattern_lines), re.M)
            self.assertRegex(stdout, regex)

        for fmt in [None, 'txt', 'rst']:
            run_test(fmt=fmt)

    def test_avail_easyconfig_templates(self):
        """Test listing available easyconfig file templates."""

        def run_test(fmt=None):
            """Helper function to test --avail-easyconfig-templates."""

            args = ['--avail-easyconfig-templates']
            if fmt is not None:
                args.append('--output-format=%s' % fmt)

            with self.mocked_stdout_stderr():
                self.eb_main(args, verbose=True, raise_error=True)
                stderr, stdout = self.get_stderr(), self.get_stdout()

            self.assertFalse(stderr)

            if fmt == 'rst':
                pattern_lines = [
                    r'^``%\(version_major\)s``\s+Major version\s*$',
                    r'^``%\(cudaver\)s``\s+full version for CUDA\s*$',
                    r'^``%\(cudamajver\)s``\s+major version for CUDA\s*$',
                    r'^``%\(pyshortver\)s``\s+short version for Python \(<major>.<minor>\)\s*$',
                    r'^\* ``%\(name\)s``$',
                    r'^``%\(namelower\)s``\s+lower case of value of name\s*$',
                    r'^``%\(arch\)s``\s+System architecture \(e.g. x86_64, aarch64, ppc64le, ...\)\s*$',
                    r'^``%\(cuda_cc_space_sep\)s``\s+Space-separated list of CUDA compute capabilities\s*$',
                    r'^``SOURCE_TAR_GZ``\s+Source \.tar\.gz bundle\s+``%\(name\)s-%\(version\)s.tar.gz``\s*$',
                    r'^``%\(software_commit\)s``\s+Git commit id to use for the software as specified '
                    'by --software-commit command line option',
                ]
            else:
                pattern_lines = [
                    r'^\s+%\(version_major\)s: Major version$',
                    r'^\s+%\(cudaver\)s: full version for CUDA$',
                    r'^\s+%\(cudamajver\)s: major version for CUDA$',
                    r'^\s+%\(pyshortver\)s: short version for Python \(<major>.<minor>\)$',
                    r'^\s+%\(name\)s$',
                    r'^\s+%\(namelower\)s: lower case of value of name$',
                    r'^\s+%\(arch\)s: System architecture \(e.g. x86_64, aarch64, ppc64le, ...\)$',
                    r'^\s+%\(cuda_cc_space_sep\)s: Space-separated list of CUDA compute capabilities$',
                    r'^\s+SOURCE_TAR_GZ: Source \.tar\.gz bundle \(%\(name\)s-%\(version\)s.tar.gz\)$',
                    r'^\s+%\(software_commit\)s: Git commit id to use for the software as specified '
                    'by --software-commit command line option',
                ]

            for pattern_line in pattern_lines:
                regex = re.compile(pattern_line, re.M)
                self.assertRegex(stdout, regex)

        for fmt in [None, 'txt', 'rst']:
            run_test(fmt=fmt)

    def test_avail_easyconfig_params(self):
        """Test listing available easyconfig parameters."""

        def run_test(custom=None, extra_params=[], fmt=None):
            """Inner function to run actual test in current setting."""

            fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
            os.close(fd)

            avail_args = [
                '-a',
                '--avail-easyconfig-params',
            ]
            for avail_arg in avail_args:

                # clear log
                write_file(self.logfile, '')

                args = [
                    '--unittest-file=%s' % self.logfile,
                    avail_arg,
                ]
                if fmt is not None:
                    args.append('--output-format=%s' % fmt)
                if custom is not None:
                    args.extend(['-e', custom])

                with self.mocked_stdout_stderr():
                    self.eb_main(args, logfile=dummylogfn, verbose=True, raise_error=True)
                logtxt = read_file(self.logfile)

                # check whether all parameter types are listed
                par_types = [BUILD, DEPENDENCIES, EXTENSIONS, FILEMANAGEMENT,
                             LICENSE, MANDATORY, MODULES, OTHER, TOOLCHAIN]
                if custom is not None:
                    par_types.append(CUSTOM)

                for param_type in [x[1] for x in par_types]:
                    # regex for parameter group title, matches both txt and rst formats
                    regex = re.compile("%s.*\n%s" % (param_type, '-' * len(param_type)), re.I)
                    tup = (param_type, avail_arg, args, logtxt)
                    msg = "Parameter type %s is featured in output of eb %s (args: %s): %s" % tup
                    self.assertRegex(logtxt, regex, msg)

                ordered_params = ['name', 'toolchain', 'version', 'versionsuffix']
                params = ordered_params + ['buildopts', 'sources', 'start_dir', 'dependencies', 'group',
                                           'exts_list', 'moduleclass', 'buildstats'] + extra_params

                # check a couple of easyconfig parameters
                param_start = 0
                for param in params:
                    # regex for parameter name (with optional '*') & description, matches both txt and rst formats
                    regex = re.compile(r"^[`]*%s(?:\*)?[`]*\s+\w+" % param, re.M)
                    tup = (param, avail_arg, args, regex.pattern, logtxt)
                    msg = "Parameter %s is listed with help in output of eb %s (args: %s, regex: %s): %s" % tup
                    res = regex.search(logtxt)
                    self.assertTrue(res, msg)
                    if param in ordered_params:
                        # check whether this parameter is listed after previous one
                        self.assertTrue(param_start < res.start(0), "%s is in expected order in: %s" % (param, logtxt))
                        param_start = res.start(0)

            if os.path.exists(dummylogfn):
                os.remove(dummylogfn)

        for fmt in [None, 'txt', 'rst']:
            run_test(fmt=fmt)
            run_test(custom='EB_foo', extra_params=['foo_extra1', 'foo_extra2'], fmt=fmt)
            run_test(custom='bar', extra_params=['bar_extra1', 'bar_extra2'], fmt=fmt)
            run_test(custom='EB_foofoo', extra_params=['foofoo_extra1', 'foofoo_extra2'], fmt=fmt)

    def test_avail_hooks(self):
        """
        Test listing available hooks via --avail-hooks
        """

        with self.mocked_stdout_stderr():
            self.eb_main(['--avail-hooks'], verbose=True, raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout()

        self.assertFalse(stderr)

        expected = '\n'.join([
            "List of supported hooks (in order of execution):",
            "	start_hook",
            "	parse_hook",
            "	pre_build_and_install_loop_hook",
            "	pre_easyblock_hook",
            "	pre_fetch_hook",
            "	post_fetch_hook",
            "	pre_ready_hook",
            "	post_ready_hook",
            "	pre_extract_hook",
            "	post_extract_hook",
            "	pre_patch_hook",
            "	post_patch_hook",
            "	pre_prepare_hook",
            "	post_prepare_hook",
            "	pre_configure_hook",
            "	post_configure_hook",
            "	pre_build_hook",
            "	post_build_hook",
            "	pre_test_hook",
            "	post_test_hook",
            "	pre_install_hook",
            "	post_install_hook",
            "	pre_extensions_hook",
            "	pre_single_extension_hook",
            "	post_single_extension_hook",
            "	post_extensions_hook",
            "	pre_postiter_hook",
            "	post_postiter_hook",
            "	pre_postproc_hook",
            "	post_postproc_hook",
            "	pre_sanitycheck_hook",
            "	post_sanitycheck_hook",
            "	pre_cleanup_hook",
            "	post_cleanup_hook",
            "	pre_module_hook",
            "	module_write_hook",
            "	post_module_hook",
            "	pre_permissions_hook",
            "	post_permissions_hook",
            "	pre_package_hook",
            "	post_package_hook",
            "	pre_testcases_hook",
            "	post_testcases_hook",
            "	post_easyblock_hook",
            "	post_build_and_install_loop_hook",
            "	end_hook",
            "	cancel_hook",
            "	crash_hook",
            "	fail_hook",
            "	pre_run_shell_cmd_hook",
            "	post_run_shell_cmd_hook",
            '',
        ])
        self.assertEqual(stdout, expected)

    # double underscore to make sure it runs first, which is required to detect certain types of bugs,
    # e.g. running with non-initialized EasyBuild config (truly mimicing 'eb --list-toolchains')
    def test__list_toolchains(self):
        """Test listing known compiler toolchains."""

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            '--list-toolchains',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)

        logtxt = read_file(self.logfile)
        self.assertIn("INFO List of known toolchains (toolchain name: module[, module, ...]):", logtxt, )
        # toolchain elements should be in alphabetical order
        tcs = {
            'system': [],
            'goalf': ['ATLAS', 'BLACS', 'FFTW', 'GCC', 'OpenMPI', 'ScaLAPACK'],
            'intel': ['icc', 'ifort', 'imkl', 'impi'],
        }
        for tc, tcelems in tcs.items():
            res = re.findall(r"^\s*%s: .*" % tc, logtxt, re.M)
            self.assertTrue(res, "Toolchain %s is included in list of known compiler toolchains" % tc)
            # every toolchain should only be mentioned once
            n = len(res)
            self.assertEqual(n, 1, "Toolchain %s is only mentioned once (count: %d)" % (tc, n))
            # make sure definition is correct (each element only named once, in alphabetical order)
            self.assertEqual("\t%s: %s" % (tc, ', '.join(tcelems)), res[0])

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_list_toolchains_rst(self):
        """Test --list-toolchains --output-format=rst."""

        args = [
            '--list-toolchains',
            '--output-format=rst',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout().strip()

        self.assertFalse(stderr)

        title = "List of known toolchains"

        # separator line: starts/ends with sequence of '=', 4 spaces in between columns
        sep_line = r'=(=+\s{4})+[=]+='

        col_names = ['Name', r'Compiler\(s\)', 'MPI', 'Linear algebra', 'FFT']
        col_names_line = r'\s+'.join(col_names) + r'\s*'

        patterns = [
            # title
            '^' + title + '\n' + '-' * len(title) + '\n',
            # header
            '\n' + '\n'.join([sep_line, col_names_line, sep_line]) + '\n',
            # compiler-only GCC toolchain
            r"\n\*\*GCC\*\*\s+GCC\s+\*\(none\)\*\s+\*\(none\)\*\s+\*\(none\)\*\s*\n",
            # gompi compiler + MPI toolchain
            r"\n\*\*gompi\*\*\s+GCC\s+OpenMPI\s+\*\(none\)\*\s+\*\(none\)\*\s*\n",
            # full 'foss' toolchain
            r"\*\*foss\*\*\s+GCC\s+OpenMPI\s+OpenBLAS,\s+ScaLAPACK\s+FFTW\s*\n",
            # compiler-only iccifort toolchain
            r"\*\*iccifort\*\*\s+icc,\s+ifort\s+\*\(none\)\*\s+\*\(none\)\*\s+\*\(none\)\*\s*\n",
            # full 'intel' toolchain (imkl appears twice, in linalg + FFT columns)
            r"\*\*intel\*\*\s+icc,\s+ifort\s+impi\s+imkl\s+imkl\s*\n",
            # fosscuda toolchain, also lists CUDA in compilers column
            r"\*\*fosscuda\*\*\s+GCC,\s+CUDA\s+OpenMPI\s+OpenBLAS,\s+ScaLAPACK\s+FFTW\s*\n",
            # system toolchain: 'none' in every column
            r"\*\*system\*\*\s+\*\(none\)\*\s+\*\(none\)\*\s+\*\(none\)\*\s+\*\(none\)\*\s*\n",
            # Cray special case
            r"\n\*\*CrayGNU\*\*\s+PrgEnv-gnu\s+cray-mpich\s+cray-libsci\s+\*\(none\)\*\s*\n",
            # footer
            '\n' + sep_line + '$',
        ]
        self._assert_regexs(patterns, stdout)

    def test_avail_lists(self):
        """Test listing available values of certain types."""

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        name_items = {
            'modules-tools': ['EnvironmentModules', 'Lmod'],
            'module-naming-schemes': ['EasyBuildMNS', 'HierarchicalMNS', 'CategorizedHMNS'],
        }
        for (name, items) in name_items.items():
            args = [
                '--avail-%s' % name,
                '--unittest-file=%s' % self.logfile,
            ]
            with self.mocked_stdout_stderr():
                self.eb_main(args, logfile=dummylogfn)
            logtxt = read_file(self.logfile)

            words = name.replace('-', ' ')
            info_msg = "INFO List of supported %s:" % words
            self.assertIn(info_msg, logtxt, "Info message with list of available %s" % words)
            for item in items:
                res = re.findall(r"^\s*%s\n" % item, logtxt, re.M)
                self.assertTrue(res, "%s is included in list of available %s" % (item, words))
                # every item should only be mentioned once
                n = len(res)
                self.assertEqual(n, 1, "%s is only mentioned once (count: %d)" % (item, n))

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_avail_cfgfile_constants(self):
        """Test --avail-cfgfile-constants."""
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # copy test easyconfigs to easybuild/easyconfigs subdirectory of temp directory
        # to check whether easyconfigs install path is auto-included in robot path
        tmpdir = tempfile.mkdtemp(prefix='easybuild-easyconfigs-pkg-install-path')
        mkdir(os.path.join(tmpdir, 'easybuild'), parents=True)

        test_ecs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs')
        copy_dir(test_ecs_dir, os.path.join(tmpdir, 'easybuild', 'easyconfigs'))

        orig_sys_path = sys.path[:]
        sys.path.insert(0, tmpdir)  # prepend to give it preference over possible other installed easyconfigs pkgs

        args = [
            '--avail-cfgfile-constants',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn)
        logtxt = read_file(self.logfile)
        cfgfile_constants = {
            'DEFAULT_ROBOT_PATHS': os.path.join(tmpdir, 'easybuild', 'easyconfigs'),
        }
        for cst_name, cst_value in cfgfile_constants.items():
            cst_regex = re.compile(r"^\*\s%s:\s.*\s\[value: .*%s.*\]" % (cst_name, cst_value), re.M)
            self.assertRegex(logtxt, cst_regex)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)
        sys.path[:] = orig_sys_path

    # use test_000_* to ensure this test is run *first*,
    # before any tests that pick up additional easyblocks (which are difficult to clean up)
    def test_000_list_easyblocks(self):
        """Test listing easyblock hierarchy."""

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # simple view
        for list_arg in ['--list-easyblocks', '--list-easyblocks=simple']:

            # clear log
            write_file(self.logfile, '')

            args = [
                list_arg,
                '--unittest-file=%s' % self.logfile,
            ]
            with self.mocked_stdout_stderr():
                self.eb_main(args, logfile=dummylogfn, raise_error=True)
            logtxt = read_file(self.logfile)

            expected = textwrap.dedent("""
                EasyBlock
                |-- bar
                |-- Bundle
                |-- CMakeMake
                |-- CmdCp
                |-- ConfigureMake
                |   |-- MakeCp
                |-- EB_binutils
                |-- EB_BLIS
                |-- EB_bzip2
                |-- EB_CMake
                |-- EB_EasyBuildMeta
                |-- EB_FFTW
                |-- EB_FFTW_period_MPI
                |-- EB_flex
                |-- EB_foo
                |   |-- EB_foofoo
                |-- EB_freetype
                |-- EB_GCC
                |-- EB_HPL
                |-- EB_libtoy
                |-- EB_libxml2
                |-- EB_LLVM
                |-- EB_Mesa
                |-- EB_OpenBLAS
                |-- EB_OpenMPI
                |-- EB_OpenSSL_wrapper
                |-- EB_Perl
                |-- EB_Python
                |-- EB_ScaLAPACK
                |-- EB_toy_buggy
                |-- EB_XCrySDen
                |-- ExtensionEasyBlock
                |   |-- DummyExtension
                |   |   |-- CustomDummyExtension
                |   |   |   |-- ChildCustomDummyExtension
                |   |   |-- DeprecatedDummyExtension
                |   |   |   |-- ChildDeprecatedDummyExtension
                |   |-- EB_toy
                |   |   |-- EB_toy_deprecated
                |   |   |-- EB_toy_eula
                |   |   |-- EB_toytoy
                |   |-- Toy_Extension
                |-- MesonNinja
                |-- ModuleRC
                |-- PerlBundle
                |-- PythonBundle
                |-- PythonPackage
                |-- Tarball
                |-- Toolchain
                Extension
                |-- ExtensionEasyBlock
                |   |-- DummyExtension
                |   |   |-- CustomDummyExtension
                |   |   |   |-- ChildCustomDummyExtension
                |   |   |-- DeprecatedDummyExtension
                |   |   |   |-- ChildDeprecatedDummyExtension
                |   |-- EB_toy
                |   |   |-- EB_toy_deprecated
                |   |   |-- EB_toy_eula
                |   |   |-- EB_toytoy
                |   |-- Toy_Extension
            """).lstrip()
            self.assertIn(expected, logtxt)

        # clear log
        write_file(self.logfile, '')

        # detailed view
        args = [
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn)
        logtxt = read_file(self.logfile)

        patterns = [
            r"EasyBlock\s+\(easybuild.framework.easyblock\)\n",
            r"\|--\s+EB_foo\s+\(easybuild.easyblocks.foo @ .*/sandbox/easybuild/easyblocks/f/foo.py\)\n" +
            r"\|\s+\|--\s+EB_foofoo\s+\(easybuild.easyblocks.foofoo @ .*/sandbox/easybuild/easyblocks/f/foofoo.py\)\n",
            r"\|--\s+bar\s+\(easybuild.easyblocks.generic.bar @ .*/sandbox/easybuild/easyblocks/generic/bar.py\)\n",
        ]
        self._assert_regexs(patterns, logtxt)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_search(self):
        """Test searching for easyconfigs."""

        test_easyconfigs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs')

        # simple search
        args = [
            '--search=gzip',
            '--robot=%s' % test_easyconfigs_dir,
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout()

        for ec in ["gzip-1.4.eb", "gzip-1.4-GCC-4.6.3.eb"]:
            regex = re.compile(r" \* \S*%s$" % ec, re.M)
            self.assertRegex(txt, regex)

        # search w/ regex
        args = [
            '--search=^gcc.*2.eb',
            '--robot=%s' % test_easyconfigs_dir,
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout()

        for ec in ['GCC-4.8.2.eb', 'GCC-4.9.2.eb']:
            regex = re.compile(r" \* \S*%s$" % ec, re.M)
            self.assertRegex(txt, regex)

        gcc_ecs = [
            'GCC-4.6.3.eb',
            'GCC-4.6.4.eb',
            'GCC-4.8.2.eb',
            'GCC-4.8.3.eb',
            'GCC-4.9.2.eb',
            'GCC-6.4.0-2.28.eb',
        ]

        # test --search-filename
        args = [
            '--search-filename=^gcc',
            '--robot=%s' % test_easyconfigs_dir,
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout()

        for ec in gcc_ecs:
            regex = re.compile(r"^ \* %s$" % ec, re.M)
            self.assertRegex(txt, regex)

        # test --search-filename --terse
        args = [
            '--search-filename=^gcc',
            '--terse',
            '--robot=%s' % test_easyconfigs_dir,
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout()

        for ec in gcc_ecs:
            regex = re.compile(r"^%s$" % ec, re.M)
            self.assertRegex(txt, regex)

        # also test --search-short/-S
        for search_arg in ['-S', '--search-short']:
            args = [
                search_arg,
                '^toy-0.0',
                '-r',
                test_easyconfigs_dir,
            ]
            with self.mocked_stdout_stderr(mock_stderr=False):
                self.eb_main(args, raise_error=True, verbose=True, testing=False)
                txt = self.get_stdout()

            self.assertRegex(txt, re.compile(r'^CFGS\d+=', re.M))
            for ec in ["toy-0.0.eb", "toy-0.0-multiple.eb"]:
                self.assertRegex(txt, r" \* \$CFGS\d+/*%s" % ec)

        # combining --search with --try-* should not cause trouble; --try-* should just be ignored
        args = [
            '--search=^gcc',
            '--robot-paths=%s' % test_easyconfigs_dir,
            '--try-toolchain-version=1.2.3',
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False, raise_error=True)
            txt = self.get_stdout()
        self.assertIn('GCC-4.9.2', txt)

        # test using a search pattern that includes special characters like '+', '(', or ')' (should not crash)
        # cfr. https://github.com/easybuilders/easybuild-framework/issues/2966
        # characters like ^, . or * are not touched, since these can be used as regex characters in queries
        for opt in ['--search', '-S', '--search-short']:
            for pattern in ['netCDF-C++', 'foo|bar', '^foo', 'foo.*bar']:
                args = [opt, pattern, '--robot', test_easyconfigs_dir]
                with self.mocked_stdout_stderr(mock_stderr=False):
                    self.eb_main(args, raise_error=True, verbose=True, testing=False)
                    stdout = self.get_stdout()
                # there shouldn't be any hits for any of these queries, so empty output...
                self.assertEqual(stdout.strip(), '')

        # some search patterns are simply invalid,
        # if they include allowed special characters like '*' but are used incorrectly...
        # a proper error is produced in that case (as opposed to a crash)
        for opt in ['--search', '-S', '--search-short']:
            for pattern in ['*foo', '(foo', ')foo', 'foo)', 'foo(']:
                args = [opt, pattern, '--robot', test_easyconfigs_dir]
                with self.mocked_stdout_stderr():
                    self.assertErrorRegex(EasyBuildError, "Invalid search query", self.eb_main, args, raise_error=True)

    def test_ignore_index(self):
        """
        Test use of --ignore-index.
        """

        test_ecs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs')
        toy_ec = os.path.join(test_ecs_dir, 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        copy_file(toy_ec, self.test_prefix)

        toy_ec_list = ['toy-0.0.eb', 'toy-1.2.3.eb', 'toy-4.5.6.eb', 'toy-11.5.6.eb']

        # install index that list more files than are actually available,
        # so we can check whether it's used
        index_txt = '\n'.join(toy_ec_list)
        write_file(os.path.join(self.test_prefix, '.eb-path-index'), index_txt)

        args = [
            '--search=toy',
            '--robot-paths=%s' % self.test_prefix,
            '--terse',
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False, raise_error=True)
            stdout = self.get_stdout()

        # Also checks for ordering: 11.x comes last!
        expected_output = '\n'.join(os.path.join(self.test_prefix, ec) for ec in toy_ec_list) + '\n'
        self.assertEqual(stdout, expected_output)

        args.append('--ignore-index')
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False, raise_error=True)
            stdout = self.get_stdout()

        # This should be the only EC found
        self.assertEqual(stdout, os.path.join(self.test_prefix, 'toy-0.0.eb') + '\n')

    def test_search_archived(self):
        "Test searching for archived easyconfigs"
        args = ['--search-filename=^intel']
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout().rstrip()
        expected = '\n'.join([
            ' * intel-compilers-2021.2.0.eb',
            ' * intel-2018a.eb',
            '',
            "Note: 1 matching archived easyconfig(s) found, use --consider-archived-easyconfigs to see them",
        ])
        self.assertEqual(txt, expected)

        args.append('--consider-archived-easyconfigs')
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, testing=False)
            txt = self.get_stdout().rstrip()
        expected = '\n'.join([
            ' * intel-compilers-2021.2.0.eb',
            ' * intel-2018a.eb',
            '',
            "Matching archived easyconfigs:",
            '',
            ' * intel-2012a.eb',
        ])
        self.assertEqual(txt, expected)

    def test_show_ec(self):
        """Test 'eb --show-ec'."""

        args = [
            '--show-ec',
            'toy-0.0.eb',
            'gzip-1.6-GCC-4.9.2.eb',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args)
            stderr, stdout = self.get_stderr(), self.get_stdout()

        self.assertFalse(stderr)
        patterns = [
            r"^== Contents of .*/test/framework/easyconfigs/test_ecs/t/toy/toy-0.0.eb:",
            r"^name = 'toy'",
            r"^toolchain = SYSTEM",
            r"^sanity_check_paths = {\n    'files': \[\('bin/yot', 'bin/toy'\)\],",
            r"^== Contents of .*/test/framework/easyconfigs/test_ecs/g/gzip/gzip-1.6-GCC-4.9.2.eb:",
            r"^easyblock = 'ConfigureMake'\n\nname = 'gzip'",
            r"^toolchain = {'name': 'GCC', 'version': '4.9.2'}",
        ]
        self._assert_regexs(patterns, stdout)

    def mocked_main(self, args, **kwargs):
        """Run eb_main with mocked stdout/stderr."""
        if not kwargs:
            kwargs = {'raise_error': True}

        stdout, stderr = self._run_mock_eb(args, **kwargs)
        self.assertEqual(stderr, '')
        return stdout.strip()

    def test_copy_ec(self):
        """Test --copy-ec."""

        topdir = os.path.dirname(os.path.abspath(__file__))
        test_easyconfigs_dir = os.path.join(topdir, 'easyconfigs', 'test_ecs')

        toy_ec_txt = read_file(os.path.join(test_easyconfigs_dir, 't', 'toy', 'toy-0.0.eb'))
        bzip2_ec_txt = read_file(os.path.join(test_easyconfigs_dir, 'b', 'bzip2', 'bzip2-1.0.6-GCC-4.9.2.eb'))

        # basic test: copying one easyconfig file to a non-existing absolute path
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        args = ['--copy-ec', 'toy-0.0.eb', test_ec]
        stdout = self.mocked_main(args)
        self.assertRegex(stdout, r'.*/toy-0.0.eb copied to %s' % test_ec)

        self.assertExists(test_ec)
        self.assertEqual(toy_ec_txt, read_file(test_ec))

        remove_file(test_ec)

        # basic test: copying one easyconfig file to a non-existing relative path
        cwd = change_dir(self.test_prefix)
        target_fn = 'test.eb'
        self.assertNotExists(target_fn)

        args = ['--copy-ec', 'toy-0.0.eb', target_fn]
        stdout = self.mocked_main(args)
        self.assertIn(r'/toy-0.0.eb copied to test.eb', stdout)

        change_dir(cwd)

        self.assertExists(test_ec)
        self.assertEqual(toy_ec_txt, read_file(test_ec))

        # copying one easyconfig into an existing directory
        test_target_dir = os.path.join(self.test_prefix, 'test_target_dir')
        mkdir(test_target_dir)
        args = ['--copy-ec', 'toy-0.0.eb', test_target_dir]
        stdout = self.mocked_main(args)
        self.assertIn('/toy-0.0.eb copied to %s' % test_target_dir, stdout)

        copied_toy_ec = os.path.join(test_target_dir, 'toy-0.0.eb')
        self.assertExists(copied_toy_ec)
        self.assertEqual(toy_ec_txt, read_file(copied_toy_ec))

        remove_dir(test_target_dir)

        def check_copied_files():
            """Helper function to check result of copying multiple easyconfigs."""
            self.assertExists(test_target_dir)
            self.assertEqual(sorted(os.listdir(test_target_dir)), ['bzip2-1.0.6-GCC-4.9.2.eb', 'toy-0.0.eb'])
            copied_toy_ec = os.path.join(test_target_dir, 'toy-0.0.eb')
            self.assertExists(copied_toy_ec)
            self.assertEqual(toy_ec_txt, read_file(copied_toy_ec))
            copied_bzip2_ec = os.path.join(test_target_dir, 'bzip2-1.0.6-GCC-4.9.2.eb')
            self.assertExists(copied_bzip2_ec)
            self.assertEqual(bzip2_ec_txt, read_file(copied_bzip2_ec))

        # copying multiple easyconfig files to a non-existing target directory (which is created automatically)
        args = ['--copy-ec', 'toy-0.0.eb', 'bzip2-1.0.6-GCC-4.9.2.eb', test_target_dir]
        stdout = self.mocked_main(args)
        self.assertEqual(stdout, '2 file(s) copied to %s' % test_target_dir)

        check_copied_files()

        remove_dir(test_target_dir)

        # same but with relative path for target dir
        change_dir(self.test_prefix)
        args[-1] = os.path.basename(test_target_dir)
        self.assertNotExists(args[-1])

        stdout = self.mocked_main(args)
        self.assertEqual(stdout, '2 file(s) copied to test_target_dir')

        check_copied_files()

        # copying multiple easyconfig to an existing target file results in an error
        target = os.path.join(self.test_prefix, 'test.eb')
        self.assertTrue(os.path.isfile(target))
        args = ['--copy-ec', 'toy-0.0.eb', 'bzip2-1.0.6-GCC-4.9.2.eb', target]
        error_pattern = ".*/test.eb exists but is not a directory"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)

        # test use of --copy-ec with only one argument: copy to current working directory
        test_working_dir = os.path.join(self.test_prefix, 'test_working_dir')
        mkdir(test_working_dir)
        change_dir(test_working_dir)
        self.assertEqual(len(os.listdir(os.getcwd())), 0)
        args = ['--copy-ec', 'toy-0.0.eb']
        stdout = self.mocked_main(args)
        regex = re.compile('.*/toy-0.0.eb copied to .*/%s' % os.path.basename(test_working_dir))
        self.assertTrue(regex.match(stdout), "Pattern '%s' found in: %s" % (regex.pattern, stdout))
        copied_toy_cwd = os.path.join(test_working_dir, 'toy-0.0.eb')
        self.assertExists(copied_toy_cwd)
        self.assertEqual(read_file(copied_toy_cwd), toy_ec_txt)

        # --copy-ec without arguments results in a proper error
        args = ['--copy-ec']
        error_pattern = "One or more files to copy should be specified!"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)

    def test_github_copy_ec_from_pr(self):
        """Test combination of --copy-ec with --from-pr."""
        if self.github_token is None:
            print("Skipping test_copy_ec_from_pr, no GitHub token available?")
            return

        test_working_dir = os.path.join(self.test_prefix, 'test_working_dir')
        mkdir(test_working_dir)
        test_target_dir = os.path.join(self.test_prefix, 'test_target_dir')
        # Make sure the test target directory doesn't exist
        remove_dir(test_target_dir)

        all_files_pr22345 = [
            'QuantumESPRESSO-7.4-foss-2024a.eb',
            'QuantumESPRESSO-7.4-parallel-symmetrization.patch',
        ]

        # test use of --copy-ec with --from-pr to the current working directory
        cwd = change_dir(test_working_dir)
        args = ['--copy-ec', '--from-pr', '22345']
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"2 file\(s\) copied to .*/%s" % os.path.basename(test_working_dir))

        # check that the files exist
        for pr_file in all_files_pr22345:
            self.assertExists(os.path.join(test_working_dir, pr_file))
            remove_file(os.path.join(test_working_dir, pr_file))

        # copying all files touched by PR to a non-existing target directory (which is created automatically)
        self.assertNotExists(test_target_dir)
        args = ['--copy-ec', '--from-pr', '22345', test_target_dir]
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"2 file\(s\) copied to .*/%s" % os.path.basename(test_target_dir))

        for pr_file in all_files_pr22345:
            self.assertExists(os.path.join(test_target_dir, pr_file))
        remove_dir(test_target_dir)

        # test where we select a single easyconfig file from a PR
        mkdir(test_target_dir)
        ec_filename = 'QuantumESPRESSO-7.4-foss-2024a.eb'
        args = ['--copy-ec', '--from-pr', '22345', ec_filename, test_target_dir]
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"%s copied to .*/%s" % (ec_filename, os.path.basename(test_target_dir)))

        self.assertEqual(os.listdir(test_target_dir), [ec_filename])
        self.assertIn("name = 'QuantumESPRESSO'", read_file(os.path.join(test_target_dir, ec_filename)))
        remove_dir(test_target_dir)

        # test copying of a single easyconfig file from a PR to a non-existing path
        environ_ec = os.path.join(self.test_prefix, 'QuantumESPRESSO.eb')
        args[-1] = environ_ec
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"%s copied to .*/QuantumESPRESSO.eb" % ec_filename)

        self.assertExists(environ_ec)
        self.assertIn("name = 'QuantumESPRESSO'", read_file(environ_ec))

        change_dir(cwd)
        remove_dir(test_working_dir)
        mkdir(test_working_dir)
        change_dir(test_working_dir)

        # test copying of a patch file from a PR via --copy-ec to current directory
        patch_fn = 'QuantumESPRESSO-7.4-parallel-symmetrization.patch'
        args = ['--copy-ec', '--from-pr', '22345', patch_fn, '.']
        stdout = self.mocked_main(args)

        self.assertEqual(os.listdir(test_working_dir), [patch_fn])
        patch_path = os.path.join(test_working_dir, patch_fn)
        self.assertExists(patch_path)
        self.assertTrue(is_patch_file(patch_path))
        remove_file(patch_path)

        # test the same thing but where we don't provide a target location
        change_dir(test_working_dir)
        args = ['--copy-ec', '--from-pr', '22345', ec_filename]
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"%s copied to .*/%s" % (ec_filename, os.path.basename(test_working_dir)))

        self.assertEqual(os.listdir(test_working_dir), [ec_filename])
        self.assertIn("name = 'QuantumESPRESSO'", read_file(os.path.join(test_working_dir, ec_filename)))

        # also test copying of patch file to current directory (without specifying target location)
        change_dir(test_working_dir)
        args = ['--copy-ec', '--from-pr', '22345', patch_fn]
        stdout = self.mocked_main(args)

        self.assertRegex(stdout, r"%s copied to .*/%s" % (patch_fn, os.path.basename(test_working_dir)))

        self.assertEqual(sorted(os.listdir(test_working_dir)), sorted([ec_filename, patch_fn]))
        self.assertTrue(is_patch_file(os.path.join(test_working_dir, patch_fn)))

        change_dir(cwd)
        remove_dir(test_working_dir)

        # test with only one ec in the PR (final argument is taken as a filename)
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        args = ['--copy-ec', '--from-pr', '21465', test_ec]
        ec_pr21465 = "EasyBuild-4.9.4.eb"
        stdout = self.mocked_main(args)
        self.assertIn(r'/%s copied to %s' % (ec_pr21465, test_ec), stdout)
        self.assertExists(test_ec)
        self.assertIn("name = 'EasyBuild'", read_file(test_ec))
        remove_file(test_ec)

    def test_copy_ec_from_commit(self):
        """Test combination of --copy-ec with --from-commit."""
        # note: --from-commit does not involve using GitHub API, so no GitHub token required

        # using easyconfigs commit to add EasyBuild-4.8.2.eb
        test_commit = '7c83a553950c233943c7b0189762f8c05cfea852'

        test_dir = os.path.join(self.test_prefix, 'from_commit')
        mkdir(test_dir, parents=True)
        args = ['--copy-ec', '--from-commit=%s' % test_commit, test_dir]
        try:
            stdout = self.mocked_main(args)
        except URLError as err:
            print("Ignoring URLError '%s' in test_copy_ec_from_commit" % err)

        pattern = "_%s/e/EasyBuild/EasyBuild-4.8.2.eb copied to " % test_commit
        self.assertIn(pattern, stdout)
        copied_ecs = os.listdir(test_dir)
        self.assertEqual(copied_ecs, ['EasyBuild-4.8.2.eb'])

        # cleanup
        remove_dir(test_dir)
        mkdir(test_dir)

        # test again, using extra argument (name of file to copy), without specifying target directory
        # (should copy to current directory)
        cwd = change_dir(test_dir)
        args = ['--copy-ec', '--from-commit=%s' % test_commit, "EasyBuild-4.8.2.eb"]
        try:
            stdout = self.mocked_main(args)
        except URLError as err:
            print("Ignoring URLError '%s' in test_copy_ec_from_commit" % err)

        self.assertIn(pattern, stdout)
        copied_ecs = os.listdir(test_dir)
        self.assertEqual(copied_ecs, ['EasyBuild-4.8.2.eb'])

        # cleanup
        change_dir(cwd)
        remove_dir(test_dir)
        mkdir(test_dir)

        # test with commit that touches a bunch of easyconfigs
        test_commit = '49c887397b1a948e1909fc24bc905fdc1ad38388'
        expected_ecs = [
            'gompi-2023b.eb',
            'gfbf-2023b.eb',
            'ScaLAPACK-2.2.0-gompi-2023b-fb.eb',
            'foss-2023b.eb',
            'HPL-2.3-foss-2023b.eb',
            'FFTW.MPI-3.3.10-gompi-2023b.eb',
            'SciPy-bundle-2023.11-gfbf-2023b.eb',
            'OSU-Micro-Benchmarks-7.2-gompi-2023b.eb',
        ]
        args = ['--copy-ec', '--from-commit=%s' % test_commit, test_dir]
        try:
            stdout = self.mocked_main(args)
        except URLError as err:
            print("Ignoring URLError '%s' in test_copy_ec_from_commit" % err)

        copied_ecs = os.listdir(test_dir)
        for ec in expected_ecs:
            self.assertIn(ec, copied_ecs)

    def test_dry_run(self):
        """Test dry run (long format)."""

        # first test with --robot
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            'gzip-1.4-GCC-4.6.3.eb',
            '--dry-run',
            '--robot',  # implies enabling dependency resolution
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn)
        logtxt = read_file(self.logfile)

        info_msg = "Dry run: printing build status of easyconfigs and dependencies"
        self.assertIn(info_msg, logtxt, "Info message dry running in '%s'" % logtxt)
        ecs_mods = [
            ("gzip-1.4-GCC-4.6.3.eb", "gzip/1.4-GCC-4.6.3", ' '),
            ("GCC-4.6.3.eb", "GCC/4.6.3", 'x'),
        ]
        for ec, mod, mark in ecs_mods:
            regex = re.compile(r" \* \[%s\] \S+%s \(module: %s\)" % (mark, ec, mod), re.M)
            self.assertRegex(logtxt, regex)

        # next test without --robot
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            'gzip-1.4-GCC-4.6.3.eb',
            '--dry-run',
            '--unittest-file=%s' % self.logfile,
        ]
        self.eb_main(args, logfile=dummylogfn)
        logtxt = read_file(self.logfile)

        info_msg = "Dry run: printing build status of easyconfigs"
        self.assertIn(info_msg, logtxt, "Info message dry running in '%s'" % logtxt)
        ec, mod, mark = ("gzip-1.4-GCC-4.6.3.eb", "gzip/1.4-GCC-4.6.3", ' ')
        regex = re.compile(r" \* \[%s\] \S+%s \(module: %s\)" % (mark, ec, mod), re.M)
        self.assertRegex(logtxt, regex)

    def test_persistence_copying_restrictions(self):
        """
        Test that EasyBuild fails when instructed to move logs or artifacts inside the build directory

        Moving log files or artifacts inside the build directory modifies the build artifacts, and in the case of
        build artifacts it is also copying directories into themselves.
        """
        base_args = [
            'gzip-1.4-GCC-4.6.3.eb',
            '--dry-run',
            '--robot',
        ]

        def test_eb_with(option_flag, is_valid):
            with tempfile.TemporaryDirectory() as root_dir:
                build_dir = os.path.join(root_dir, 'build_dir')
                if is_valid:
                    persist_path = os.path.join(root_dir, 'persist_dir')
                else:
                    persist_path = os.path.join(root_dir, 'build_dir', 'persist_dir')

                extra_args = [
                    f"--buildpath={build_dir}",
                    f"{option_flag}={persist_path}",
                ]

                pattern = rf"The {option_flag} \(.*\) cannot reside in a subdirectory of the --buildpath \(.*\)"

                args = base_args
                args.extend(extra_args)

                if is_valid:
                    try:
                        self.eb_main(args, raise_error=True)
                    except EasyBuildError:
                        self.fail(
                            "Should not fail with --buildpath='{build_dir}' and {option_flag}='{persist_path}'."
                        )
                else:
                    self.assertErrorRegex(EasyBuildError, pattern, self.eb_main, args, raise_error=True)

        test_eb_with(option_flag='--failed-install-logs-path', is_valid=True)
        test_eb_with(option_flag='--failed-install-logs-path', is_valid=False)
        test_eb_with(option_flag='--failed-install-build-dirs-path', is_valid=True)
        test_eb_with(option_flag='--failed-install-build-dirs-path', is_valid=False)

    def test_missing(self):
        """Test use of --missing/-M."""

        for mns in [None, 'HierarchicalMNS']:

            args = ['gzip-1.4-GCC-4.6.3.eb']

            if mns == 'HierarchicalMNS':
                args.append('--module-naming-scheme=%s' % mns)
                expected = '\n'.join([
                    "4 out of 4 required modules missing:",
                    '',
                    "* Core | GCC/4.6.3 (GCC-4.6.3.eb)",
                    "* Core | intel/2018a (intel-2018a.eb)",
                    "* Core | toy/.0.0-deps (toy-0.0-deps.eb)",
                    "* Compiler/GCC/4.6.3 | gzip/1.4 (gzip-1.4-GCC-4.6.3.eb)",
                    '',
                ])
            else:
                expected = '\n'.join([
                    "1 out of 4 required modules missing:",
                    '',
                    "* gzip/1.4-GCC-4.6.3 (gzip-1.4-GCC-4.6.3.eb)",
                    '',
                ])

            for opt in ['-M', '--missing-modules']:
                with self.mocked_stdout_stderr():
                    self.eb_main(args + [opt], testing=False, raise_error=True)
                    stderr, stdout = self.get_stderr(), self.get_stdout()
                self.assertFalse(stderr)
                self.assertIn(expected, stdout)
            # --terse
            with self.mocked_stdout_stderr():
                self.eb_main(args + ['-M', '--terse'], testing=False, raise_error=True)
                stderr, stdout = self.get_stderr(), self.get_stdout()
            self.assertFalse(stderr)
            if mns == 'HierarchicalMNS':
                expected = '\n'.join([
                    "GCC-4.6.3.eb",
                    "intel-2018a.eb",
                    "toy-0.0-deps.eb",
                    "gzip-1.4-GCC-4.6.3.eb",
                ])
            else:
                expected = 'gzip-1.4-GCC-4.6.3.eb'
            self.assertEqual(stdout, expected + '\n')

    def test_dry_run_short(self):
        """Test dry run (short format)."""
        # unset $EASYBUILD_ROBOT_PATHS that was defined in setUp
        del os.environ['EASYBUILD_ROBOT_PATHS']

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # copy test easyconfigs to easybuild/easyconfigs subdirectory of temp directory
        # to check whether easyconfigs install path is auto-included in robot path
        tmpdir = tempfile.mkdtemp(prefix='easybuild-easyconfigs-pkg-install-path')
        mkdir(os.path.join(tmpdir, 'easybuild'), parents=True)

        test_ecs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        copy_dir(test_ecs_dir, os.path.join(tmpdir, 'easybuild', 'easyconfigs'))

        orig_sys_path = sys.path[:]
        sys.path.insert(0, tmpdir)  # prepend to give it preference over possible other installed easyconfigs pkgs

        robot_decoy = os.path.join(self.test_prefix, 'robot_decoy')
        mkdir(robot_decoy)
        for dry_run_arg in ['-D', '--dry-run-short']:
            write_file(self.logfile, '')
            args = [
                os.path.join(tmpdir, 'easybuild', 'easyconfigs', 'g', 'gzip', 'gzip-1.4-GCC-4.6.3.eb'),
                dry_run_arg,
                # purposely specifying senseless dir, to test auto-inclusion of easyconfigs pkg path in robot path
                '--robot=%s' % robot_decoy,
                '--unittest-file=%s' % self.logfile,
            ]
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)

            info_msg = r"Dry run: printing build status of easyconfigs and dependencies"
            self.assertIn(info_msg, outtxt)
            self.assertIn('CFGS=', outtxt)
            ecs_mods = [
                ("gzip-1.4-GCC-4.6.3.eb", "gzip/1.4-GCC-4.6.3", ' '),
                ("GCC-4.6.3.eb", "GCC/4.6.3", 'x'),
            ]
            for ec, mod, mark in ecs_mods:
                regex = re.compile(r" \* \[%s\] \$CFGS\S+%s \(module: %s\)" % (mark, ec, mod), re.M)
                self.assertRegex(outtxt, regex)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

        # cleanup
        shutil.rmtree(tmpdir)
        sys.path[:] = orig_sys_path

    def test_try_robot_force(self):
        """
        Test correct behavior for combination of --try-toolchain --robot --force.
        Only the listed easyconfigs should be forced, resolved dependencies should not (even if tweaked).
        """
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # use toy-0.0.eb easyconfig file that comes with the tests
        test_ecs = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        eb1 = os.path.join(test_ecs, 'f', 'FFTW', 'FFTW-3.3.7-gompi-2018a.eb')
        eb2 = os.path.join(test_ecs, 's', 'ScaLAPACK', 'ScaLAPACK-2.0.2-gompi-2018a-OpenBLAS-0.2.20.eb')

        # check log message with --skip for existing module
        args = [
            eb1,
            eb2,
            '--debug',
            '--force',
            '--robot=%s' % test_ecs,
            '--try-toolchain=gompi,2018b',
            '--dry-run',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, logfile=dummylogfn)

        scalapack_ver = '2.0.2-gompi-2018b-OpenBLAS-0.2.20'
        ecs_mods = [
            # GCC/OpenMPI dependencies are there, but part of toolchain => 'x'
            ("GCC-7.3.0-2.30.eb", "GCC/7.3.0-2.30", 'x'),
            ("OpenMPI-3.1.1-GCC-7.3.0-2.30.eb", "OpenMPI/3.1.1-GCC-7.3.0-2.30", 'x'),
            # toolchain used for OpenBLAS is mapped to GCC/7.3.0-2.30 subtoolchain in gompi/2018b
            # (rather than the original GCC/6.4.0-2.28 as subtoolchain of gompi/2018a)
            ("OpenBLAS-0.2.20-GCC-7.3.0-2.30.eb", "OpenBLAS/0.2.20-GCC-7.3.0-2.30", 'x'),
            # both FFTW and ScaLAPACK are listed => 'F'
            ("ScaLAPACK-%s.eb" % scalapack_ver, "ScaLAPACK/%s" % scalapack_ver, 'F'),
            ("FFTW-3.3.7-gompi-2018b.eb", "FFTW/3.3.7-gompi-2018b", 'F'),
        ]
        for ec, mod, mark in ecs_mods:
            regex = re.compile(r"^ \* \[%s\] \S+%s \(module: %s\)$" % (mark, ec, mod), re.M)
            self.assertRegex(outtxt, regex)

    def test_try_toolchain_mapping(self):
        """Test mapping of subtoolchains with --try-toolchain."""
        test_ecs = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        gzip_ec = os.path.join(test_ecs, 'g', 'gzip', 'gzip-1.5-foss-2018a.eb')

        args = [
            gzip_ec,
            '--try-toolchain=iccifort,2016.1.150-GCC-4.9.3-2.25',
            '--dry-run',
            '--robot',
        ]

        # by default, toolchain mapping is enabled
        # if it fails, an error is printed
        error_pattern = "Toolchain iccifort is not equivalent to toolchain foss in terms of capabilities."
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True, do_build=True)

        # can continue anyway using --disable-map-toolchains
        args.append('--disable-map-toolchains')
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True, do_build=True)

        patterns = [
            r"^ \* \[ \] .*/iccifort-2016.1.150-GCC-4.9.3-2.25.eb \(module: iccifort/.*\)$",
            r"^ \* \[ \] .*/gzip-1.5-iccifort-2016.1.150-GCC-4.9.3-2.25.eb \(module: gzip/1.5-iccifort.*\)$",
        ]
        self._assert_regexs(patterns, outtxt)

        anti_patterns = [
            r"^ \* \[.\] .*-foss-2018a",
            r"^ \* \[.\] .*-gompi-2018a",
            r"^ \* \[.\] .*-GCC.*6\.4\.0",
        ]
        self._assert_regexs(anti_patterns, outtxt, assert_true=False)

    def test_try_update_deps(self):
        """Test for --try-update-deps."""

        # first, construct a toy easyconfig that is well suited for testing (multiple deps)
        test_ectxt = '\n'.join([
            "easyblock = 'ConfigureMake'",
            '',
            "name = 'test'",
            "version = '1.2.3'",
            ''
            "homepage = 'https://test.org'",
            "description = 'this is just a test'",
            '',
            "toolchain = {'name': 'GCC', 'version': '4.9.3-2.26'}",
            '',
            "builddependencies = [('gzip', '1.4')]",
            "dependencies = [('hwloc', '1.6.2')]",
        ])
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        write_file(test_ec, test_ectxt)

        args = [
            test_ec,
            '--try-toolchain-version=6.4.0-2.28',
            '--try-update-deps',
            '-D',
            '--robot',
        ]

        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, "Experimental functionality", self.eb_main, args, raise_error=True)

        args.append('--experimental')
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True, do_build=True)

        patterns = [
            # toolchain got updated
            r"^ \* \[x\] .*/test_ecs/g/GCC/GCC-6.4.0-2.28.eb \(module: GCC/6.4.0-2.28\)$",
            # no version update for gzip (because there's no gzip easyconfig using GCC/6.4.0-2.28 (sub)toolchain)
            r"^ \* \[ \] .*/tweaked_dep_easyconfigs/gzip-1.4-GCC-6.4.0-2.28.eb \(module: gzip/1.4-GCC-6.4.0-2.28\)$",
            # hwloc was updated to 1.11.8, thanks to available easyconfig
            r"^ \* \[x\] .*/test_ecs/h/hwloc/hwloc-1.11.8-GCC-6.4.0-2.28.eb \(module: hwloc/1.11.8-GCC-6.4.0-2.28\)$",
            # also generated easyconfig for test/1.2.3 with expected toolchain
            r"^ \* \[ \] .*/tweaked_easyconfigs/test-1.2.3-GCC-6.4.0-2.28.eb \(module: test/1.2.3-GCC-6.4.0-2.28\)$",
        ]
        self._assert_regexs(patterns, outtxt)

        # construct another toy easyconfig that is well suited for testing ignoring versionsuffix
        test_ectxt = '\n'.join([
            "easyblock = 'ConfigureMake'",
            '',
            "name = 'test'",
            "version = '1.2.3'",
            ''
            "homepage = 'https://test.org'",
            "description = 'this is just a test'",
            '',
            "toolchain = {'name': 'GCC', 'version': '4.8.2'}",
            '',
            "dependencies = [('OpenBLAS', '0.2.8', '-LAPACK-3.4.2')]",
        ])
        write_file(test_ec, test_ectxt)
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True, do_build=True)
            errtxt = self.get_stderr()
        warning_stub = "\nWARNING: There may be newer version(s) of dep 'OpenBLAS' available with a different " \
                       "versionsuffix to '-LAPACK-3.4.2'"
        self.assertIn(warning_stub, errtxt)
        patterns = [
            # toolchain got updated
            r"^ \* \[x\] .*/test_ecs/g/GCC/GCC-6.4.0-2.28.eb \(module: GCC/6.4.0-2.28\)$",
            # no version update for OpenBLAS (because there's no corresponding ec using GCC/6.4.0-2.28 (sub)toolchain)
            r"^ \* \[ \] .*/tweaked_dep_easyconfigs/OpenBLAS-0.2.8-GCC-6.4.0-2.28-LAPACK-3.4.2.eb "
            r"\(module: OpenBLAS/0.2.8-GCC-6.4.0-2.28-LAPACK-3.4.2\)$",
            # also generated easyconfig for test/1.2.3 with expected toolchain
            r"^ \* \[ \] .*/tweaked_easyconfigs/test-1.2.3-GCC-6.4.0-2.28.eb \(module: test/1.2.3-GCC-6.4.0-2.28\)$",
        ]
        self._assert_regexs(patterns, outtxt)

        # Now verify that we can ignore versionsuffixes
        args.append('--try-ignore-versionsuffixes')
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True, do_build=True)
        patterns = [
            # toolchain got updated
            r"^ \* \[x\] .*/test_ecs/g/GCC/GCC-6.4.0-2.28.eb \(module: GCC/6.4.0-2.28\)$",
            # no version update for OpenBLAS (because there's no corresponding ec using GCC/6.4.0-2.28 (sub)toolchain)
            r"^ \* \[x\] .*/test_ecs/o/OpenBLAS/OpenBLAS-0.2.20-GCC-6.4.0-2.28.eb "
            r"\(module: OpenBLAS/0.2.20-GCC-6.4.0-2.28\)$",
            # also generated easyconfig for test/1.2.3 with expected toolchain
            r"^ \* \[ \] .*/tweaked_easyconfigs/test-1.2.3-GCC-6.4.0-2.28.eb \(module: test/1.2.3-GCC-6.4.0-2.28\)$",
        ]
        self._assert_regexs(patterns, outtxt)

    def test_dry_run_hierarchical(self):
        """Test dry run using a hierarchical module naming scheme."""
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            'gzip-1.5-foss-2018a.eb',
            'OpenMPI-2.1.2-GCC-6.4.0-2.28.eb',
            '--dry-run',
            '--robot',
            '--unittest-file=%s' % self.logfile,
            '--module-naming-scheme=HierarchicalMNS',
            '--ignore-osdeps',
            '--force',
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, logfile=dummylogfn, verbose=True, raise_error=True)

        ecs_mods = [
            # easyconfig, module subdir, (short) module name
            ("GCC-6.4.0-2.28.eb", "Core", "GCC/6.4.0-2.28", 'x'),  # already present but not listed, so 'x'
            ("hwloc-1.11.8-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28", "hwloc/1.11.8", 'x'),
            # already present and listed, so 'F'
            ("OpenMPI-2.1.2-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28", "OpenMPI/2.1.2", 'F'),
            ("gompi-2018a.eb", "Core", "gompi/2018a", 'x'),
            ("OpenBLAS-0.2.20-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28", "OpenBLAS/0.2.20", ' '),
            ("FFTW-3.3.7-gompi-2018a.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2", "FFTW/3.3.7", 'x'),
            ("ScaLAPACK-2.0.2-gompi-2018a-OpenBLAS-0.2.20.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2",
             "ScaLAPACK/2.0.2-OpenBLAS-0.2.20", 'x'),
            ("foss-2018a.eb", "Core", "foss/2018a", 'x'),
            # listed but not there: ' '
            ("gzip-1.5-foss-2018a.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2", "gzip/1.5", ' '),
        ]
        for ec, mod_subdir, mod_name, mark in ecs_mods:
            regex = re.compile(r"^ \* \[%s\] \S+%s \(module: %s \| %s\)$" % (mark, ec, mod_subdir, mod_name), re.M)
            self.assertRegex(outtxt, regex)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_dry_run_categorized(self):
        """Test dry run using a categorized hierarchical module naming scheme."""
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        self.setup_categorized_hmns_modules()
        args = [
            'gzip-1.5-foss-2018a.eb',
            'OpenMPI-2.1.2-GCC-6.4.0-2.28.eb',
            '--dry-run',
            '--robot',
            '--unittest-file=%s' % self.logfile,
            '--module-naming-scheme=CategorizedHMNS',
            '--ignore-osdeps',
            '--force',
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, logfile=dummylogfn, verbose=True, raise_error=True)

        ecs_mods = [
            # easyconfig, module subdir, (short) module name, mark
            ("GCC-6.4.0-2.28.eb", "Core/compiler", "GCC/6.4.0-2.28", 'x'),  # already present but not listed, so 'x'
            ("hwloc-1.11.8-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28/system", "hwloc/1.11.8", 'x'),
            # already present and listed, so 'F'
            ("OpenMPI-2.1.2-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28/mpi", "OpenMPI/2.1.2", 'F'),
            ("gompi-2018a.eb", "Core/toolchain", "gompi/2018a", 'x'),
            ("OpenBLAS-0.2.20-GCC-6.4.0-2.28.eb", "Compiler/GCC/6.4.0-2.28/numlib",
             "OpenBLAS/0.2.20", 'x'),
            ("FFTW-3.3.7-gompi-2018a.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2/numlib", "FFTW/3.3.7", 'x'),
            ("ScaLAPACK-2.0.2-gompi-2018a-OpenBLAS-0.2.20.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2/numlib",
             "ScaLAPACK/2.0.2-OpenBLAS-0.2.20", 'x'),
            ("foss-2018a.eb", "Core/toolchain", "foss/2018a", 'x'),
            # listed but not there: ' '
            ("gzip-1.5-foss-2018a.eb", "MPI/GCC/6.4.0-2.28/OpenMPI/2.1.2/tools", "gzip/1.5", ' '),
        ]
        for ec, mod_subdir, mod_name, mark in ecs_mods:
            regex = re.compile(r"^ \* \[%s\] \S+%s \(module: %s \| %s\)$" % (mark, ec, mod_subdir, mod_name), re.M)
            self.assertRegex(outtxt, regex)

        if os.path.exists(dummylogfn):
            os.remove(dummylogfn)

    def test_github_from_pr(self):
        """Test fetching easyconfigs from a PR."""
        if self.github_token is None:
            print("Skipping test_from_pr, no GitHub token available?")
            return

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        tmpdir = tempfile.mkdtemp()
        args = [
            # PR for XCrySDen/1.6.2-foss-2024a, see https://github.com/easybuilders/easybuild-easyconfigs/pull/22227
            '--from-pr=22227',
            '--dry-run',
            # an argument must be specified to --robot, since easybuild-easyconfigs may not be installed
            '--robot=%s' % os.path.join(os.path.dirname(__file__), 'easyconfigs'),
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # a GitHub token should be available for this user
            '--tmpdir=%s' % tmpdir,
        ]
        try:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
            modules = [
                (tmpdir, 'XCrySDen/1.6.2-foss-2024a'),
            ]
            for path_prefix, module in modules:
                ec_fn = "%s.eb" % '-'.join(module.split('/'))
                path = '.*%s' % os.path.dirname(path_prefix)
                regex = re.compile(r"^ \* \[.\] %s.*%s \(module: %s\)$" % (path, ec_fn, module), re.M)
                self.assertRegex(outtxt, regex)

            pr_tmpdir = os.path.join(tmpdir, r'eb-\S{6,8}', 'files_pr22227')
            self.assertRegex(outtxt, r"Extended list of robot search paths with \['%s'\]:" % pr_tmpdir)
        except URLError as err:
            print("Ignoring URLError '%s' in test_from_pr" % err)
            shutil.rmtree(tmpdir)

        # test with multiple prs
        tmpdir = tempfile.mkdtemp()
        args = [
            # PRs for various easyconfigs
            '--from-pr=22227,19834',
            '--dry-run',
            # an argument must be specified to --robot, since easybuild-easyconfigs may not be installed
            '--robot=%s' % os.path.join(os.path.dirname(__file__), 'easyconfigs'),
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # a GitHub token should be available for this user
            '--tmpdir=%s' % tmpdir,
        ]
        try:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
            modules = [
                (tmpdir, 'quarto/1.5.57-x64'),
                (tmpdir, 'Gblocks/0.91b'),
            ]
            for path_prefix, module in modules:
                ec_fn = "%s.eb" % '-'.join(module.split('/'))
                path = '.*%s' % os.path.dirname(path_prefix)
                regex = re.compile(r"^ \* \[.\] %s.*%s \(module: %s\)$" % (path, ec_fn, module), re.M)
                self.assertRegex(outtxt, regex)

            for pr in ('22227', '19834'):
                pr_tmpdir = os.path.join(tmpdir, r'eb-\S{6,8}', 'files_pr%s' % pr)
                self.assertRegex(outtxt, r"Extended list of robot search paths with .*%s.*:" % pr_tmpdir)

        except URLError as err:
            print("Ignoring URLError '%s' in test_from_pr" % err)
            shutil.rmtree(tmpdir)

    def test_github_from_pr_token_log(self):
        """Check that --from-pr doesn't leak GitHub token in log."""
        if self.github_token is None:
            print("Skipping test_from_pr_token_log, no GitHub token available?")
            return

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            # PR for XCrySDen/1.6.2-foss-2024a, see https://github.com/easybuilders/easybuild-easyconfigs/pull/22227
            '--from-pr=22227',
            '--dry-run',
            '--debug',
            # an argument must be specified to --robot, since easybuild-easyconfigs may not be installed
            '--robot=%s' % os.path.join(os.path.dirname(__file__), 'easyconfigs'),
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # a GitHub token should be available for this user
        ]
        try:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
                stdout = self.get_stdout()
                stderr = self.get_stderr()
            self.assertNotIn(self.github_token, outtxt)
            self.assertNotIn(self.github_token, stdout)
            self.assertNotIn(self.github_token, stderr)

        except URLError as err:
            print("Ignoring URLError '%s' in test_from_pr" % err)

    def test_github_from_pr_listed_ecs(self):
        """Test --from-pr in combination with specifying easyconfigs on the command line."""
        if self.github_token is None:
            print("Skipping test_from_pr, no GitHub token available?")
            return

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # copy test easyconfigs to easybuild/easyconfigs subdirectory of temp directory
        test_ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        ecstmpdir = tempfile.mkdtemp(prefix='easybuild-easyconfigs-pkg-install-path')
        mkdir(os.path.join(ecstmpdir, 'easybuild'), parents=True)
        copy_dir(test_ecs_path, os.path.join(ecstmpdir, 'easybuild', 'easyconfigs'))

        # inject path to test easyconfigs into head of Python search path
        sys.path.insert(0, ecstmpdir)

        tmpdir = tempfile.mkdtemp()
        args = [
            'toy-0.0.eb',
            'XCrySDen-1.6.2-foss-2024a.eb',
            'GCC-4.6.3.eb',
            # PR for XCrySDen/1.6.2-foss-2024a, see https://github.com/easybuilders/easybuild-easyconfigs/pull/22227
            '--from-pr=22227',
            '--dry-run',
            # an argument must be specified to --robot, since easybuild-easyconfigs may not be installed
            '--robot=%s' % test_ecs_path,
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # a GitHub token should be available for this user
            '--tmpdir=%s' % tmpdir,
        ]
        try:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
            modules = [
                (test_ecs_path, 'toy/0.0'),  # not included in PR
                ('.*%s' % os.path.dirname(tmpdir), 'XCrySDen/1.6.2-foss-2024a'),
                ('.*%s' % os.path.dirname(tmpdir), 'Togl/2.0-GCCcore-13.3.0'),
                (test_ecs_path, 'GCC/4.6.3'),  # not included in PR, available locally
            ]
            for path_prefix, module in modules:
                ec_fn = "%s.eb" % '-'.join(module.split('/'))
                regex = re.compile(r"^ \* \[.\] %s.*%s \(module: %s\)$" % (path_prefix, ec_fn, module), re.M)
                self.assertRegex(outtxt, regex)

            # make sure that *only* these modules are listed, no others
            regex = re.compile(r"^ \* \[.\] .*/(?P<filepath>.*) \(module: (?P<module>.*)\)$", re.M)
            self.assertTrue(sorted(regex.findall(outtxt)), sorted(modules))

        except URLError as err:
            print("Ignoring URLError '%s' in test_from_pr" % err)
            shutil.rmtree(tmpdir)

    def test_github_from_pr_x(self):
        """Test combination of --from-pr with --extended-dry-run."""
        if self.github_token is None:
            print("Skipping test_from_pr_x, no GitHub token available?")
            return

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            # PR for XCrySDen/1.6.2-foss-2024a, see https://github.com/easybuilders/easybuild-easyconfigs/pull/22227
            '--from-pr=22227',
            'XCrySDen-1.6.2-foss-2024a.eb',
            # an argument must be specified to --robot, since easybuild-easyconfigs may not be installed
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # a GitHub token should be available for this user
            '--tmpdir=%s' % self.test_prefix,
            '--extended-dry-run',
        ]
        try:
            with self.mocked_stdout_stderr():
                self.eb_main(args, do_build=True, raise_error=True, testing=False)
                stdout = self.get_stdout()

            msg_regexs = [
                r"^== Build succeeded for 1 out of 1",
                r"^\*\*\* DRY RUN using 'EB_XCrySDen' easyblock",
                r"^== building and installing XCrySDen/1.6.2-foss-2024a\.\.\.",
                r"^building... \[DRY RUN\]",
                r"^== COMPLETED: Installation ended successfully \(took .* secs?\)",
            ]

            self._assert_regexs(msg_regexs, stdout)

        except URLError as err:
            print("Ignoring URLError '%s' in test_from_pr_x" % err)

    def test_from_commit(self):
        """Test for --from-commit."""
        # --from-commit does not involve using GitHub API, so no GitHub token required;
        # however, because we easily hit GitHub rate limits when using --from-commit
        # (see also https://github.blog/changelog/2025-05-08-updated-rate-limits-for-unauthenticated-requests/),
        # we only run this test when a GitHub token is available,
        # which is only the case for a limited number of test configurations (see .github/workflows/unit_tests.yml)
        if self.github_token is None:
            print("Skipping test_from_commit (no GitHub token available)")
            return

        # easyconfigs commit to add EasyBuild-4.8.2.eb
        test_commit = '7c83a553950c233943c7b0189762f8c05cfea852'

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        tmpdir = tempfile.mkdtemp()
        args = [
            '--from-commit=%s' % test_commit,
            '--dry-run',
            '--tmpdir=%s' % tmpdir,
        ]
        try:
            outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
            modules = [
                (tmpdir, 'EasyBuild/4.8.2'),
            ]
            for path_prefix, module in modules:
                ec_fn = "%s.eb" % '-'.join(module.split('/'))
                path = '.*%s' % os.path.dirname(path_prefix)
                regex = re.compile(r"^ \* \[.\] %s.*%s \(module: %s\)$" % (path, ec_fn, module), re.M)
                self.assertRegex(outtxt, regex)

            # make sure that *only* these modules are listed, no others
            regex = re.compile(r"^ \* \[.\] .*/(?P<filepath>.*) \(module: (?P<module>.*)\)$", re.M)
            self.assertTrue(sorted(regex.findall(outtxt)), sorted(modules))

            pr_tmpdir = os.path.join(tmpdir, r'eb-\S{6,8}', 'files_commit_%s' % test_commit)
            self.assertRegex(outtxt, r"Extended list of robot search paths with \['%s'\]:" % pr_tmpdir)
        except URLError as err:
            print("Ignoring URLError '%s' in test_from_commit" % err)
            shutil.rmtree(tmpdir)

        easyblock_template = '\n'.join([
            "from easybuild.framework.easyblock import EasyBlock",
            "class %s(EasyBlock):",
            "    pass",
        ])

        # create fake custom easyblock for CMake that is required by easyconfig used in test below
        easyblock_file = os.path.join(self.test_prefix, 'easyblocks', 'cmake.py')
        write_file(easyblock_file, easyblock_template % 'EB_CMake')

        # also test with an easyconfig that requires additional easyconfigs to resolve dependencies,
        # cfr. https://github.com/easybuilders/easybuild-framework/issues/4540;
        # using commit that adds CMake-3.18.4.eb (which requires ncurses-6.2.eb),
        # see https://github.com/easybuilders/easybuild-easyconfigs/pull/13156
        test_commit = '41eee3fe2e5102f52319481ca8dde16204dab590'
        args = [
            '--from-commit=%s' % test_commit,
            '--dry-run',
            '--robot',
            '--tmpdir=%s' % tmpdir,
            '--include-easyblocks=' + os.path.join(self.test_prefix, 'easyblocks', '*.py'),
        ]
        try:
            outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
            modules = [
                (tmpdir, 'ncurses/6.2'),
                (tmpdir, 'CMake/3.18.4'),
            ]
            for path_prefix, module in modules:
                ec_fn = "%s.eb" % '-'.join(module.split('/'))
                path = '.*%s' % os.path.dirname(path_prefix)
                regex = re.compile(r"^ \* \[.\] %s.*%s \(module: %s\)$" % (path, ec_fn, module), re.M)
                self.assertRegex(outtxt, regex)

            # make sure that *only* these modules are listed, no others
            regex = re.compile(r"^ \* \[.\] .*/(?P<filepath>.*) \(module: (?P<module>.*)\)$", re.M)
            self.assertEqual(sorted(x[1] for x in regex.findall(outtxt)), sorted(x[1] for x in modules))

            pr_tmpdir = os.path.join(tmpdir, r'eb-\S{6,8}', 'files_commit_%s' % test_commit)
            self.assertRegex(outtxt, r"Extended list of robot search paths with \['%s'\]:" % pr_tmpdir)
        except URLError as err:
            print("Ignoring URLError '%s' in test_from_commit" % err)
            shutil.rmtree(tmpdir)

    # must be run after test for --list-easyblocks, hence the '_xxx_'
    # cleaning up the imported easyblocks is quite difficult...
    def test_xxx_include_easyblocks_from_commit(self):
        """Test for --include-easyblocks-from-commit."""
        # --from-commit does not involve using GitHub API, so no GitHub token required;
        # however, because we easily hit GitHub rate limits when using --from-commit
        # (see also https://github.blog/changelog/2025-05-08-updated-rate-limits-for-unauthenticated-requests/),
        # we only run this test when a GitHub token is available,
        # which is only the case for a limited number of test configurations (see .github/workflows/unit_tests.yml)
        if self.github_token is None:
            print("Skipping test_xxx_include_easyblocks_from_commit (no GitHub token available)")
            return

        orig_local_sys_path = sys.path[:]
        # easyblocks commit only touching Binary easyblock
        test_commit = '94d28c556947bd96d0978df775b15a50a4600c6f'

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        tmpdir = tempfile.mkdtemp()
        args = [
            '--include-easyblocks-from-commit=%s' % test_commit,
            '--dry-run',
            '--tmpdir=%s' % tmpdir,
            'toy-0.0.eb',  # test easyconfig
        ]
        try:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, logfile=dummylogfn, raise_error=True)
                stdout = self.get_stdout()
                stderr = self.get_stderr()

            # 'undo' import of foo easyblock
            del sys.modules['easybuild.easyblocks.generic.binary']
            sys.path[:] = orig_local_sys_path
            import easybuild.easyblocks
            reload(easybuild.easyblocks)
            import easybuild.easyblocks.generic
            reload(easybuild.easyblocks.generic)

            pattern = "== easyblock binary.py included from commit %s" % test_commit
            self.assertEqual(stderr, '')
            self.assertIn(pattern, stdout)

            regex = re.compile(r"^ \* \[.\] .*/toy-0.0.eb \(module: toy/0.0\)$", re.M)
            self.assertRegex(outtxt, regex)

        except URLError as err:
            print("Ignoring URLError '%s' in test_include_easyblocks_from_commit" % err)
            shutil.rmtree(tmpdir)

    def test_no_such_software(self):
        """Test using no arguments."""

        args = [
            '--software-name=nosuchsoftware',
            '--robot=.',
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args)

        # error message when template is not found
        error_msg1 = "ERROR.* No easyconfig files found for software nosuchsoftware, and no templates available. "
        error_msg1 += "I'm all out of ideas."
        # error message when template is found
        error_msg2 = "ERROR Unable to find an easyconfig for the given specifications"
        regex = re.compile("(%s|%s)" % (error_msg1, error_msg2))
        self.assertRegex(outtxt, regex)

    def test_header_footer(self):
        """Test specifying a module header/footer."""

        # create file containing modules footer
        if get_module_syntax() == 'Tcl':
            modules_header_txt = '\n'.join([
                "# test header",
                "setenv SITE_SPECIFIC_HEADER_ENV_VAR foo",
            ])
            modules_footer_txt = '\n'.join([
                "# test footer",
                "setenv SITE_SPECIFIC_FOOTER_ENV_VAR bar",
            ])
        elif get_module_syntax() == 'Lua':
            modules_header_txt = '\n'.join([
                "-- test header",
                'setenv("SITE_SPECIFIC_HEADER_ENV_VAR", "foo")',
            ])
            modules_footer_txt = '\n'.join([
                "-- test footer",
                'setenv("SITE_SPECIFIC_FOOTER_ENV_VAR", "bar")',
            ])
        else:
            self.fail("Unknown module syntax: %s" % get_module_syntax())

        # dump header/footer text to file
        handle, modules_footer = tempfile.mkstemp(prefix='modules-footer-')
        os.close(handle)
        write_file(modules_footer, modules_footer_txt)
        handle, modules_header = tempfile.mkstemp(prefix='modules-header-')
        os.close(handle)
        write_file(modules_header, modules_header_txt)

        # use toy-0.0.eb easyconfig file that comes with the tests
        eb_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # check log message with --skip for existing module
        args = [
            eb_file,
            '--debug',
            '--force',
            '--modules-header=%s' % modules_header,
            '--modules-footer=%s' % modules_footer,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)

        toy_module = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            toy_module += '.lua'
        toy_module_txt = read_file(toy_module)

        regex = re.compile(r'%s$' % modules_header_txt.replace('(', '\\(').replace(')', '\\)'), re.M)
        msg = "modules header '%s' is present in '%s'" % (modules_header_txt, toy_module_txt)
        self.assertRegex(toy_module_txt, regex, msg)

        regex = re.compile(r'%s$' % modules_footer_txt.replace('(', '\\(').replace(')', '\\)'), re.M)
        msg = "modules footer '%s' is present in '%s'" % (modules_footer_txt, toy_module_txt)
        self.assertRegex(toy_module_txt, regex, msg)

        # cleanup
        os.remove(modules_footer)
        os.remove(modules_header)

    def test_recursive_module_unload(self):
        """Test generating recursively unloading modules."""

        # use toy-0.0.eb easyconfig file that comes with the tests
        eb_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-deps.eb')

        # check log message with --skip for existing module
        lastargs = ['--recursive-module-unload']
        if self.modtool.supports_depends_on:
            lastargs.append('--module-depends-on')
        for lastarg in lastargs:
            args = [
                eb_file,
                '--debug',
                '--force',
                lastarg,
            ]
            with self.mocked_stdout_stderr():
                self.eb_main(args, do_build=True, verbose=True)

            toy_module = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0-deps')

            if get_module_syntax() == 'Lua':
                toy_module += '.lua'
                is_loaded_msg = 'if not ( isloaded("gompi/2018a") )'
            else:
                # Tcl syntax
                is_loaded_msg = "if { ![is-loaded gompi/2018a] }"

            toy_module_txt = read_file(toy_module)
            self.assertNotIn(is_loaded_msg, toy_module_txt, "Recursive unloading is used: %s" % toy_module_txt)

    def test_tmpdir(self):
        """Test setting temporary directory to use by EasyBuild."""

        # use temporary paths for build/install paths, make sure sources can be found
        tmpdir = tempfile.mkdtemp()

        # use toy-0.0.eb easyconfig file that comes with the tests
        eb_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # check log message with --skip for existing module
        args = [
            eb_file,
            '--debug',
            '--tmpdir=%s' % tmpdir,
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, reset_env=False)

        tmpdir_msg = r"Using %s\S+ as temporary directory" % os.path.join(tmpdir, 'eb-')
        found = re.search(tmpdir_msg, outtxt, re.M)
        self.assertTrue(found, "Log message for tmpdir found in outtxt: %s" % outtxt)

        for var in ['TMPDIR', 'TEMP', 'TMP']:
            self.assertTrue(os.environ[var].startswith(os.path.join(tmpdir, 'eb-')))
        self.assertTrue(tempfile.gettempdir().startswith(os.path.join(tmpdir, 'eb-')))
        tempfile_tmpdir = tempfile.mkdtemp()
        self.assertTrue(tempfile_tmpdir.startswith(os.path.join(tmpdir, 'eb-')))
        fd, tempfile_tmpfile = tempfile.mkstemp()
        self.assertTrue(tempfile_tmpfile.startswith(os.path.join(tmpdir, 'eb-')))

        # cleanup
        os.close(fd)
        shutil.rmtree(tmpdir)

    def test_ignore_osdeps(self):
        """Test ignoring of listed OS dependencies."""
        txt = '\n'.join([
            'easyblock = "ConfigureMake"',
            'name = "pi"',
            'version = "3.14"',
            'homepage = "http://example.com"',
            'description = "test easyconfig"',
            'toolchain = SYSTEM',
            'osdependencies = ["nosuchosdependency", ("nosuchdep_option1", "nosuchdep_option2")]',
        ])
        fd, eb_file = tempfile.mkstemp(prefix='easyconfig_test_file_', suffix='.eb')
        os.close(fd)
        write_file(eb_file, txt)

        # check whether non-existing OS dependencies result in failure, by default
        args = [
            eb_file,
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True)

        self.assertIn("Checking OS dependencies", outtxt)
        msg = "One or more OS dependencies were not found: "
        msg += "[('nosuchosdependency',), ('nosuchdep_option1', 'nosuchdep_option2')]"
        self.assertIn(msg, outtxt, "OS dependencies are honored, outtxt: %s" % outtxt)

        # check whether OS dependencies are effectively ignored
        args = [
            eb_file,
            '--ignore-osdeps',
            '--dry-run',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True)

        self.assertIn("Not checking OS dependencies", outtxt,
                      "OS dependencies are ignored with --ignore-osdeps, outtxt: %s" % outtxt)

        txt += "\nstop = 'notavalidstop'"
        write_file(eb_file, txt)
        args = [
            eb_file,
            '--dry-run',  # no explicit --ignore-osdeps, but implied by --dry-run
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True)

        self.assertIn("stop provided 'notavalidstop' is not valid", outtxt,
                      "Validations are performed with --ignore-osdeps, outtxt: %s" % outtxt)

    def test_experimental(self):
        """Test the experimental option"""
        orig_value = easybuild.tools.build_log.EXPERIMENTAL
        # make sure it's off by default
        self.assertFalse(orig_value)

        log = fancylogger.getLogger()

        # force it to False
        EasyBuildOptions(
            go_args=['--disable-experimental'],
        )
        try:
            log.experimental('x')
            # sanity check, should never be reached if it works.
            self.fail("Experimental logging should be disabled by setting --disable-experimental option")
        except easybuild.tools.build_log.EasyBuildError as err:
            # check error message
            self.assertIn('Experimental functionality.', str(err))

        # toggle experimental
        EasyBuildOptions(
            go_args=['--experimental'],
        )
        try:
            log.experimental('x')
        except easybuild.tools.build_log.EasyBuildError as err:
            self.fail("Experimental logging should be allowed by the --experimental option: %s" % err)

        # set it back
        easybuild.tools.build_log.EXPERIMENTAL = orig_value

    def test_deprecated(self):
        """Test the deprecated option"""
        if 'EASYBUILD_DEPRECATED' in os.environ:
            os.environ['EASYBUILD_DEPRECATED'] = str(VERSION)
            init_config()

        orig_value = easybuild.tools.build_log.CURRENT_VERSION

        # make sure it's off by default
        self.assertEqual(orig_value, VERSION)

        log = fancylogger.getLogger()

        # force it to lower version using 0.x, which should no result in any raised error (only deprecation logging)
        EasyBuildOptions(
            go_args=['--deprecated=0.%s' % orig_value],
        )
        stderr = None
        try:
            with self.mocked_stdout_stderr():
                log.deprecated('x', str(orig_value))
                stderr = self.get_stderr()
        except easybuild.tools.build_log.EasyBuildError as err:
            self.fail("Deprecated logging should work: %s" % err)

        stderr_regex = re.compile("^\nWARNING: Deprecated functionality, will no longer work in")
        self.assertRegex(stderr, stderr_regex)

        # force it to current version, which should result in deprecation
        EasyBuildOptions(
            go_args=['--deprecated=%s' % orig_value],
        )
        try:
            log.deprecated('x', str(orig_value))
            # not supposed to get here
            self.fail('Deprecated logging should throw EasyBuildError')
        except easybuild.tools.build_log.EasyBuildError as err2:
            self.assertIn('DEPRECATED', str(err2))

        # force higher version by prefixing it with 1, which should result in deprecation errors
        EasyBuildOptions(
            go_args=['--deprecated=1%s' % orig_value],
        )
        try:
            log.deprecated('x', str(orig_value))
            # not supposed to get here
            self.fail('Deprecated logging should throw EasyBuildError')
        except easybuild.tools.build_log.EasyBuildError as err3:
            self.assertIn('DEPRECATED', str(err3))

        # set it back
        easybuild.tools.build_log.CURRENT_VERSION = orig_value

    def test_allow_modules_tool_mismatch(self):
        """Test allowing mismatch of modules tool with 'module' function."""
        # make sure MockModulesTool is available
        from test.framework.modulestool import MockModulesTool  # noqa

        # trigger that main() creates new instance of ModulesTool
        self.modtool = None

        topdir = os.path.abspath(os.path.dirname(__file__))
        ec_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # keep track of original module definition so we can restore it
        orig_module = os.environ.get('module', None)

        # check whether mismatch between 'module' function and selected modules tool is detected
        os.environ['module'] = "() {  eval `/Users/kehoste/Modules/$MODULE_VERSION/bin/modulecmd bash $*`\n}"
        args = [
            ec_file,
            '--modules-tool=MockModulesTool',
            '--module-syntax=Tcl',  # Lua would require Lmod
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True)
        outtxt = read_file(self.logfile)
        error_regex = "ERROR .*pattern .* not found in defined 'module' function"
        self.assertRegex(outtxt, error_regex, "Found error w.r.t. module function mismatch: %s" % outtxt[-600:])

        # check that --allow-modules-tool-mispatch transforms this error into a warning
        os.environ['module'] = "() {  eval `/Users/kehoste/Modules/$MODULE_VERSION/bin/modulecmd bash $*`\n}"
        args = [
            ec_file,
            '--modules-tool=MockModulesTool',
            '--module-syntax=Tcl',  # Lua would require Lmod
            '--allow-modules-tool-mismatch',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True)
        outtxt = read_file(self.logfile)
        warn_regex = "WARNING .*pattern .* not found in defined 'module' function"
        self.assertRegex(outtxt, warn_regex, "Found warning w.r.t. module function mismatch: %s" % outtxt[-600:])

        # check whether match between 'module' function and selected modules tool is detected
        os.environ['module'] = "() {  eval ` /bin/echo $*`\n}"
        args = [
            ec_file,
            '--modules-tool=MockModulesTool',
            '--module-syntax=Tcl',  # Lua would require Lmod
            '--debug',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True)
        outtxt = read_file(self.logfile)
        found_regex = "DEBUG Found pattern .* in defined 'module' function"
        self.assertRegex(outtxt, found_regex, "Found debug message w.r.t. module function: %s" % outtxt[-600:])

        # restore 'module' function
        if orig_module is not None:
            os.environ['module'] = orig_module
        else:
            del os.environ['module']

    def test_try(self):
        """Test whether --try options are taken into account."""
        ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        tweaked_toy_ec = os.path.join(self.test_buildpath, 'toy-0.0-tweaked.eb')
        copy_file(os.path.join(ecs_path, 't', 'toy', 'toy-0.0.eb'), tweaked_toy_ec)
        write_file(tweaked_toy_ec, "easyblock = 'ConfigureMake'", append=True)

        args = [
            tweaked_toy_ec,
            '--dry-run',
            '--robot=%s' % ecs_path,
        ]

        test_cases = [
            ([], 'toy/0.0'),
            # try-* only uses the subtoolchain with matching necessary features
            (['--try-software=foo,1.2.3', '--try-toolchain=gompi,2018a'], 'foo/1.2.3-GCC-6.4.0-2.28'),
            (['--try-toolchain-name=gompi', '--try-toolchain-version=2018a'], 'toy/0.0-GCC-6.4.0.2.28'),
            # --try-toolchain is overridden by --toolchain
            (['--try-toolchain=gompi,2018a', '--toolchain=system,system'], 'toy/0.0'),
            # check we interpret SYSTEM correctly as a toolchain
            (['--try-toolchain=SYSTEM'], 'toy/0.0'),
            (['--toolchain=SYSTEM'], 'toy/0.0'),
            (['--try-software-name=foo', '--try-software-version=1.2.3'], 'foo/1.2.3'),
            (['--try-toolchain-name=gompi', '--try-toolchain-version=2018a'], 'toy/0.0-GCC-6.4.0.2.28'),
            (['--try-software-version=1.2.3', '--try-toolchain=gompi,2018a'], 'toy/1.2.3-GCC-6.4.0.2.28'),
            (['--try-amend=versionsuffix=-test'], 'toy/0.0-test'),
            # --try-amend is overridden by --amend
            (['--amend=versionsuffix=', '--try-amend=versionsuffix=-test'], 'toy/0.0'),
            (['--try-toolchain=gompi,2018a', '--toolchain=system,system'], 'toy/0.0'),
            # tweak existing list-typed value (patches)
            (['--try-amend=versionsuffix=-test2', '--try-amend=patches=1.patch,2.patch'], 'toy/0.0-test2'),
            # append to existing list-typed value (patches)
            (['--try-amend=versionsuffix=-test3', '--try-amend=patches=,extra.patch'], 'toy/0.0-test3'),
            # prepend to existing list-typed value (patches)
            (['--try-amend=versionsuffix=-test4', '--try-amend=patches=extra.patch,'], 'toy/0.0-test4'),
            # define extra list-typed parameter
            (['--try-amend=versionsuffix=-test5', '--try-amend=exts_list=1,2,3'], 'toy/0.0-test5'),
            # only --try causes other build specs to be included too
            (['--try-software=foo,1.2.3', '--toolchain=gompi,2018a'], 'foo/1.2.3-GCC-6.4.0-2.28'),
            (['--software=foo,1.2.3', '--try-toolchain=gompi,2018a'], 'foo/1.2.3-GCC-6.4.0-2.28'),
            (['--software=foo,1.2.3', '--try-amend=versionsuffix=-test'], 'foo/1.2.3-test'),
        ]

        for extra_args, mod in test_cases:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args + extra_args, verbose=True, raise_error=True)
            mod_regex = re.compile(r"\(module: %s\)$" % mod, re.M)
            self.assertRegex(outtxt, mod_regex)

        for extra_arg in ['--try-software=foo', '--try-toolchain=gompi', '--try-toolchain=gomp,2018a,-a-suffix']:
            allargs = args + [extra_arg]
            with self.mocked_stdout_stderr():
                self.assertErrorRegex(EasyBuildError, "problems validating the options",
                                      self.eb_main, allargs, raise_error=True)

        # no --try used, so no tweaked easyconfig files are generated
        allargs = args + ['--software-version=1.2.3', '--toolchain=gompi,2018a']
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, "version .* not available", self.eb_main, allargs, raise_error=True)

        # Try changing only name or version of toolchain
        args.pop(0)  # Remove EC filename
        foss_toy_ec = os.path.join(self.test_buildpath, 'toy-0.0-foss-2018a.eb')
        copy_file(os.path.join(ecs_path, 't', 'toy', 'toy-0.0-gompi-2018a.eb'), foss_toy_ec)
        write_file(foss_toy_ec, "toolchain['name'] = 'foss'", append=True)

        test_cases = [
            (['toy-0.0-gompi-2018a.eb', '--try-toolchain-name=intel'], 'toy/0.0-iimpi-2018a'),
            ([foss_toy_ec, '--try-toolchain-name=intel'], 'toy/0.0-intel-2018a'),
            (['toy-0.0-gompi-2018a.eb', '--try-toolchain-version=2018b'], 'toy/0.0-gompi-2018b'),
        ]
        for extra_args, mod in test_cases:
            outtxt = self.eb_main(args + extra_args, verbose=True, raise_error=True)
            mod_regex = re.compile(r"\(module: %s\)$" % mod, re.M)
            self.assertRegex(outtxt, mod_regex)

    def test_try_with_copy(self):
        """Test whether --try options are taken into account."""
        ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        tweaked_toy_ec = os.path.join(self.test_buildpath, 'toy-0.0-tweaked.eb')
        copy_file(os.path.join(ecs_path, 't', 'toy', 'toy-0.0.eb'), tweaked_toy_ec)
        write_file(tweaked_toy_ec, "easyblock = 'ConfigureMake'", append=True)

        args = [
            tweaked_toy_ec,
            '--dry-run',
            '--robot=%s' % ecs_path,
            '--copy-ec',
        ]
        copied_ec = os.path.join(self.test_buildpath, 'my_eb.eb')
        with self.mocked_stdout_stderr():
            self.eb_main(args + [copied_ec], verbose=True, raise_error=True)
            outtxt = self.get_stdout()
            errtxt = self.get_stderr()
        self.assertIn(r'toy-0.0-tweaked.eb copied to ' + copied_ec, outtxt)
        self.assertFalse(errtxt)
        self.assertExists(copied_ec)

        tweaked_ecs_dir = os.path.join(self.test_buildpath, 'my_tweaked_ecs')
        with self.mocked_stdout_stderr():
            self.eb_main(args + ['--try-software=foo,1.2.3', '--try-toolchain=gompi,2018a', tweaked_ecs_dir],
                         verbose=True, raise_error=True)
            outtxt = self.get_stdout()
            errtxt = self.get_stderr()
        self.assertIn(r'1 file(s) copied to ' + tweaked_ecs_dir, outtxt)
        self.assertFalse(errtxt)
        self.assertExists(os.path.join(self.test_buildpath, tweaked_ecs_dir, 'foo-1.2.3-GCC-6.4.0-2.28.eb'))

    def test_software_version_ordering(self):
        """Test whether software versions are correctly ordered when using --software."""
        ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')

        gcc_ec = os.path.join(ecs_path, 'g', 'GCC', 'GCC-4.9.2.eb')

        test_gcc_ec = os.path.join(self.test_prefix, 'GCC-4.10.1.eb')
        test_gcc_txt = read_file(gcc_ec).replace("version = '4.9.2'", "version = '4.10.1'")

        write_file(test_gcc_ec, test_gcc_txt)

        args = [
            '--software=GCC,4.10.1',
            '--dry-run',
            '--robot=%s:%s' % (ecs_path, self.test_prefix),
        ]
        with self.mocked_stdout_stderr():
            out = self.eb_main(['--software=GCC,4.10.1'] + args[1:], raise_error=True)

        regex = re.compile(r"GCC-4.10.1.eb \(module: GCC/4.10.1\)$", re.M)
        self.assertRegex(out, regex)

    def test_recursive_try(self):
        """Test whether recursive --try-X works."""
        ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        tweaked_toy_ec = os.path.join(self.test_buildpath, 'toy-0.0-tweaked.eb')
        copy_file(os.path.join(ecs_path, 't', 'toy', 'toy-0.0.eb'), tweaked_toy_ec)
        write_file(tweaked_toy_ec, "dependencies = [('gzip', '1.4')]\n", append=True)  # add fictious dependency

        sourcepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox', 'sources')
        args = [
            tweaked_toy_ec,
            '--sourcepath=%s' % sourcepath,
            '--try-toolchain=gompi,2018a',
            '--robot=%s' % ecs_path,
            '--ignore-osdeps',
            '--dry-run',
        ]

        for extra_args in [[], ['--module-naming-scheme=HierarchicalMNS']]:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args + extra_args, verbose=True, raise_error=True)
            # toolchain GCC/4.7.2 (subtoolchain of gompi/2018a) should be listed (and present)

            tc_regex = re.compile(r"^ \* \[x\] .*/GCC-6.4.0-2.28.eb \(module: .*GCC/6.4.0-2.28\)$", re.M)
            self.assertRegex(outtxt, tc_regex)

            # both toy and gzip dependency should be listed with new toolchains
            # in this case we map original toolchain `dummy` to the compiler-only GCC subtoolchain of gompi/2018a
            # since this subtoolchain already has sufficient capabilities (we do not map higher than necessary)
            for ec_name in ['gzip-1.4', 'toy-0.0']:
                ec = '%s-GCC-6.4.0-2.28.eb' % ec_name
                if extra_args:
                    mod = ec_name.replace('-', '/')
                else:
                    mod = '%s-GCC-6.4.0-2.28' % ec_name.replace('-', '/')
                mod_regex = re.compile(r"^ \* \[ \] \S+/eb-\S+/%s \(module: .*%s\)$" % (ec, mod), re.M)
                self.assertRegex(outtxt, mod_regex)

        # recursive try also when --(try-)software(-X) is involved
        for extra_args in [[],
                           ['--module-naming-scheme=HierarchicalMNS']]:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args + extra_args + ['--try-software-version=1.2.3'], verbose=True,
                                      raise_error=True)

            # toolchain GCC/6.4.0-2.28 (subtoolchain of gompi/2018a) should be listed (and present)
            tc_regex = re.compile(r"^ \* \[x\] .*/GCC-6.4.0-2.28.eb \(module: .*GCC/6.4.0-2.28\)$", re.M)
            self.assertRegex(outtxt, tc_regex)

            # both toy and gzip dependency should be listed with new toolchains
            # in this case we map original toolchain `dummy` to the compiler-only GCC subtoolchain of gompi/2018a
            # since this subtoolchain already has sufficient capabilities (we do not map higher than necessary)
            for ec_name in ['gzip-1.4', 'toy-1.2.3']:
                ec = '%s-GCC-6.4.0-2.28.eb' % ec_name
                mod = ec_name.replace('-', '/')
                if not extra_args:
                    mod += '-GCC-6.4.0-2.28'
                mod_regex = re.compile(r"^ \* \[ \] \S+/eb-\S+/%s \(module: .*%s\)$" % (ec, mod), re.M)
                self.assertRegex(outtxt, mod_regex)

        # clear fictitious dependency
        write_file(tweaked_toy_ec, "dependencies = []\n", append=True)

        # no recursive try if --disable-map-toolchains is involved
        for extra_args in [['--try-software-version=1.2.3'], ['--software-version=1.2.3']]:
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args + ['--disable-map-toolchains'] + extra_args, raise_error=True)
            for mod in ['toy/1.2.3-gompi-2018a', 'gompi/2018a', 'GCC/6.4.0-2.28']:
                mod_regex = re.compile(r"\(module: %s\)$" % mod, re.M)
                self.assertRegex(outtxt, mod_regex)
            for mod in ['gompi/1.2.3', 'GCC/1.2.3']:
                mod_regex = re.compile(r"\(module: %s\)$" % mod, re.M)
                self.assertNotRegex(outtxt, mod_regex)

    def test_cleanup_builddir(self):
        """Test cleaning up of build dir and --disable-cleanup-builddir."""
        toy_ec = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        toy_buildpath = os.path.join(self.test_buildpath, 'toy', '0.0', 'system-system')

        args = [
            toy_ec,
            '--force',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, verbose=True)

        # make sure build directory is properly cleaned up after a successful build (default behavior)
        self.assertFalse(os.path.exists(toy_buildpath), "Build dir %s removed after successful build" % toy_buildpath)
        # make sure --disable-cleanup-builddir works
        args.append('--disable-cleanup-builddir')
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, verbose=True)
        self.assertExists(toy_buildpath, "Build dir %s is retained when requested" % toy_buildpath)
        shutil.rmtree(toy_buildpath)

        # make sure build dir stays in case of failed build
        args = [
            toy_ec,
            '--force',
            '--try-amend=prebuildopts=nosuchcommand &&',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True)
        self.assertExists(toy_buildpath, "Build dir %s is retained after failed build" % toy_buildpath)

    def test_filter_deps(self):
        """Test use of --filter-deps."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        ec_file = os.path.join(test_dir, 'easyconfigs', 'test_ecs', 'f', 'foss', 'foss-2018a.eb')
        os.environ['MODULEPATH'] = os.path.join(test_dir, 'modules')
        args = [
            ec_file,
            '--robot=%s' % os.path.join(test_dir, 'easyconfigs'),
            '--dry-run',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)

        # note: using loose regex pattern when we expect no match, strict pattern when we do expect a match
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        # clear log file
        write_file(self.logfile, '')

        # filter deps (including a non-existing dep, i.e. zlib)
        args.extend(['--filter-deps', 'FFTW,ScaLAPACK,zlib'])
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertNotIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        # filter specific version of deps
        args[-1] = 'FFTW=3.2.3,zlib,ScaLAPACK=2.0.2'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertNotIn('module: ScaLAPACK', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        args[-1] = 'zlib,FFTW=3.3.7,ScaLAPACK=2.0.1'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        # filter deps with version range: only filter FFTW 3.x, ScaLAPACK 1.x
        args[-1] = 'zlib,ScaLAPACK=]1.0:2.0[,FFTW=[3.0:4.0['
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        # also test open ended ranges
        args[-1] = 'zlib,ScaLAPACK=[1.0:,FFTW=:4.0['
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW', outtxt)
        self.assertNotIn('module: ScaLAPACK', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        args[-1] = 'zlib,ScaLAPACK=[2.1:,FFTW=:3.0['
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        # test corner cases where version to filter in equal to low/high range limit
        args[-1] = 'FFTW=[3.3.7:4.0],zlib,ScaLAPACK=[1.0:2.0.2]'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW', outtxt)
        self.assertNotIn('module: ScaLAPACK', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        # FFTW & ScaLAPACK versions are not included in range, so no filtering
        args[-1] = 'FFTW=]3.3.7:4.0],zlib,ScaLAPACK=[1.0:2.0.2['
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')

        # also test mix of ranges & specific versions
        args[-1] = 'FFTW=3.3.7,zlib,ScaLAPACK=[1.0:2.0.2['
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertNotIn('module: FFTW', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        write_file(self.logfile, '')
        args[-1] = 'FFTW=]3.3.7:4.0],zlib,ScaLAPACK=2.0.2'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertNotIn('module: ScaLAPACK', outtxt)
        self.assertNotIn('module: zlib', outtxt)

        # This easyconfig contains a dependency of CMake for which no easyconfig exists. It should still
        # succeed when called with --filter-deps=CMake=:2.8.10]
        write_file(self.logfile, '')
        ec_file = os.path.join(test_dir, 'easyconfigs', 'test_ecs', 'f', 'foss', 'foss-2018a-broken.eb')
        args[0] = ec_file
        args[-1] = 'FFTW=3.3.7,CMake=:2.8.10],zlib'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        # dictionaries can be printed in any order
        regexp = "filtered out dependency.*('name': 'CMake'.*'version': '2.8.10'|'version': '2.8.10'.*'name': 'CMake')"
        self.assertRegex(outtxt, regexp)

        # The test below fails without PR 2983
        write_file(self.logfile, '')
        ec_file = os.path.join(test_dir, 'easyconfigs', 'test_ecs', 'f', 'foss', 'foss-2018a-broken.eb')
        args[0] = ec_file
        args[-1] = 'FFTW=3.3.7,CMake=:2.8.10],zlib'
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args + ['--minimal-toolchains'], do_build=True, verbose=True, raise_error=True)
        self.assertRegex(outtxt, regexp)

    def test_hide_deps(self):
        """Test use of --hide-deps."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        ec_file = os.path.join(test_dir, 'easyconfigs', 'test_ecs', 'f', 'foss', 'foss-2018a.eb')
        os.environ['MODULEPATH'] = os.path.join(test_dir, 'modules')
        args = [
            ec_file,
            '--robot=%s' % os.path.join(test_dir, 'easyconfigs'),
            '--dry-run',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: GCC/6.4.0-2.28', outtxt)
        self.assertIn('module: OpenMPI/2.1.2-GCC-6.4.0-2.28', outtxt)
        self.assertIn('module: OpenBLAS/0.2.20-GCC-6.4.0-2.28', outtxt)
        self.assertIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        # zlib is not a dep at all
        self.assertNotIn('module: zlib', outtxt)

        # clear log file
        write_file(self.logfile, '')

        # hide deps (including a non-existing dep, i.e. zlib)
        args.append('--hide-deps=FFTW,ScaLAPACK,zlib')
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, do_build=True, verbose=True, raise_error=True)
        self.assertIn('module: GCC/6.4.0-2.28', outtxt)
        self.assertIn('module: OpenMPI/2.1.2-GCC-6.4.0-2.28', outtxt)
        self.assertIn('module: OpenBLAS/0.2.20-GCC-6.4.0-2.28', outtxt)
        self.assertNotIn('module: FFTW/3.3.7-gompi', outtxt)
        self.assertIn('module: FFTW/.3.3.7-gompi', outtxt)
        self.assertNotIn('module: ScaLAPACK/2.0.2-gompi', outtxt)
        self.assertIn('module: ScaLAPACK/.2.0.2-gompi', outtxt)
        # zlib is not a dep at all
        self.assertNotIn('module: zlib', outtxt)

    def test_hide_toolchains(self):
        """Test use of --hide-toolchains."""
        test_ecs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        ec_file = os.path.join(test_ecs_dir, 'g', 'gzip', 'gzip-1.6-GCC-4.9.2.eb')
        args = [
            ec_file,
            '--dry-run',
            '--robot',
            '--hide-toolchains=GCC',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args)
        self.assertIn(r'module: GCC/.4.9.2', outtxt)
        self.assertIn(r'module: gzip/1.6-GCC-4.9.2', outtxt)

    def test_parse_http_header_fields_urlpat(self):
        """Test function parse_http_header_fields_urlpat"""
        urlex = "example.com"
        urlgnu = "gnu.org"
        hdrauth = "Authorization"
        valauth = "Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="
        hdragent = "User-Agent"
        valagent = "James/0.0.7 (MI6)"
        hdrrefer = "Referer"
        valrefer = "http://www.example.com/"
        filesub1 = os.path.join(self.test_prefix, "testhttpheaders1.txt")
        filesub2 = os.path.join(self.test_prefix, "testhttpheaders2.txt")
        filesub3 = os.path.join(self.test_prefix, "testhttpheaders3.txt")
        filesub4 = os.path.join(self.test_prefix, "testhttpheaders4.txt")
        fileauth = os.path.join(self.test_prefix, "testhttpheadersauth.txt")
        write_file(filesub4, filesub3)
        write_file(filesub3, filesub2)
        write_file(filesub2, filesub1)
        write_file(filesub1, "%s::%s:%s\n" % (urlgnu, hdrauth, valauth))
        write_file(filesub2, "%s::%s\n" % (urlex, filesub1))
        write_file(filesub3, "%s::%s:%s\n" % (urlex, hdragent, filesub2))
        write_file(fileauth, "%s\n" % (valauth))

        # Case A: basic pattern
        args = "%s::%s:%s" % (urlgnu, hdragent, valagent)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlgnu: ["%s:%s" % (hdragent, valagent)]}, urlpat_headers)

        # Case B: urlpat has another urlpat: retain deepest level
        args = "%s::%s::%s::%s:%s" % (urlgnu, urlgnu, urlex, hdragent, valagent)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlex: ["%s:%s" % (hdragent, valagent)]}, urlpat_headers)

        # Case C: header value has a colon
        args = "%s::%s:%s" % (urlex, hdrrefer, valrefer)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlex: ["%s:%s" % (hdrrefer, valrefer)]}, urlpat_headers)

        # Case D: recurse into files
        args = filesub3
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlgnu: ["%s:%s" % (hdrauth, valauth)]}, urlpat_headers)

        # Case E: recurse into files as header
        args = "%s::%s" % (urlex, filesub3)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlgnu: ["%s:%s" % (hdrauth, valauth)]}, urlpat_headers)

        # Case F: recurse into files as value (header is replaced)
        args = "%s::%s:%s" % (urlex, hdrrefer, filesub3)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlgnu: ["%s:%s" % (hdrauth, valauth)]}, urlpat_headers)

        # Case G: recurse into files as value (header is retained)
        args = "%s::%s:%s" % (urlgnu, hdrauth, fileauth)
        urlpat_headers = parse_http_header_fields_urlpat(args)
        self.assertEqual({urlgnu: ["%s:%s" % (hdrauth, valauth)]}, urlpat_headers)

        # Case H: recurse into files but hit limit
        args = filesub4
        error_regex = r"Failed to parse_http_header_fields_urlpat \(recursion limit\)"
        self.assertErrorRegex(EasyBuildError, error_regex, parse_http_header_fields_urlpat, args)

        # Case I: argument is not a string
        args = list("foobar")
        error_regex = r"Failed to parse_http_header_fields_urlpat \(argument not a string\)"
        self.assertErrorRegex(EasyBuildError, error_regex, parse_http_header_fields_urlpat, args)

    def test_http_header_fields_urlpat(self):
        """Test use of --http-header-fields-urlpat."""
        tmpdir = tempfile.mkdtemp()
        test_ecs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        gzip_ec = os.path.join(test_ecs_dir, 'g', 'gzip', 'gzip-1.6-GCC-4.9.2.eb')
        gzip_ec_txt = read_file(gzip_ec)
        regex = re.compile('^source_urls = .*', re.M)
        test_ec_txt = regex.sub("source_urls = ['https://sources.easybuild.io/g/gzip']", gzip_ec_txt)
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        write_file(test_ec, test_ec_txt)
        common_args = [
            test_ec,
            '--stop=fetch',
            '--debug',
            '--force',
            '--force-download',
            '--logtostdout',
            '--sourcepath=%s' % tmpdir,
        ]

        # define header fields:values that should (not) show up in the logs, either
        # because they are secret or because they are not matched for the url
        testdohdr = 'HeaderAPPLIED'
        testdoval = 'SECRETvalue'
        testdonthdr = 'HeaderIGNORED'
        testdontval = 'BOGUSvalue'

        # header fields (or its values) could be files to be read instead of literals
        testcmdfile = os.path.join(self.test_prefix, 'testhttpheaderscmdline.txt')
        testincfile = os.path.join(self.test_prefix, 'testhttpheadersvalinc.txt')
        testexcfile = os.path.join(self.test_prefix, 'testhttpheadersvalexc.txt')
        testinchdrfile = os.path.join(self.test_prefix, 'testhttpheadershdrinc.txt')
        testexchdrfile = os.path.join(self.test_prefix, 'testhttpheadershdrexc.txt')
        testurlpatfile = os.path.join(self.test_prefix, 'testhttpheadersurlpat.txt')

        # log mention format upon header or file inclusion
        mentionhdr = 'Custom HTTP header field set: %s'
        mentionfile = 'File included in parse_http_header_fields_urlpat: %s'

        def run_and_assert(args, msg, words_expected=None, words_unexpected=None):
            stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)
            if words_expected is not None:
                self._assert_regexs(words_expected, stdout)
            if words_unexpected is not None:
                self._assert_regexs(words_unexpected, stdout, assert_true=False)

        # A: simple direct case (all is logged because passed directly via EasyBuild configuration options)
        args = list(common_args)
        args.extend([
            '--http-header-fields-urlpat=easybuild.io::%s:%s' % (testdohdr, testdoval),
            '--http-header-fields-urlpat=nomatch.com::%s:%s' % (testdonthdr, testdontval),
        ])
        # expect to find everything passed on cmdline
        expected = [mentionhdr % (testdohdr), testdoval, testdonthdr, testdontval]
        run_and_assert(args, "case A", expected)

        # all subsequent tests share this argument list
        args = common_args
        args.append('--http-header-fields-urlpat=%s' % (testcmdfile))

        # B: simple file case (secrets in file are not logged)
        txt = '\n'.join([
            'easybuild.io::%s: %s' % (testdohdr, testdoval),
            'nomatch.com::%s: %s' % (testdonthdr, testdontval),
            '',
        ])
        write_file(testcmdfile, txt)
        # expect to find only the header key (not its value) and only for the appropriate url
        expected = [mentionhdr % testdohdr, mentionfile % testcmdfile]
        not_expected = [testdoval, testdonthdr, testdontval]
        run_and_assert(args, "case B", expected, not_expected)

        # C: recursion one: header value is another file
        txt = '\n'.join([
            'easybuild.io::%s: %s' % (testdohdr, testincfile),
            'nomatch.com::%s: %s' % (testdonthdr, testexcfile),
            '',
        ])
        write_file(testcmdfile, txt)
        write_file(testincfile, '%s\n' % (testdoval))
        write_file(testexcfile, '%s\n' % (testdontval))
        # expect to find only the header key (not its value and not the filename) and only for the appropriate url
        expected = [mentionhdr % (testdohdr), mentionfile % (testcmdfile),
                    mentionfile % (testincfile), mentionfile % (testexcfile)]
        not_expected = [testdoval, testdonthdr, testdontval]
        run_and_assert(args, "case C", expected, not_expected)

        # D: recursion two: header field+value is another file,
        write_file(testcmdfile, '\n'.join([
            'easybuild.io::%s' % (testinchdrfile),
            'nomatch.com::%s' % (testexchdrfile),
            '',
        ]))
        write_file(testinchdrfile, '%s: %s\n' % (testdohdr, testdoval))
        write_file(testexchdrfile, '%s: %s\n' % (testdonthdr, testdontval))
        # expect to find only the header key (and the literal filename) and only for the appropriate url
        expected = [mentionhdr % (testdohdr), mentionfile % (testcmdfile),
                    mentionfile % (testinchdrfile), mentionfile % (testexchdrfile)]
        not_expected = [testdoval, testdonthdr, testdontval]
        run_and_assert(args, "case D", expected, not_expected)

        # E: recursion three: url pattern + header field + value in another file
        write_file(testcmdfile, '%s\n' % (testurlpatfile))
        txt = '\n'.join([
            'easybuild.io::%s: %s' % (testdohdr, testdoval),
            'nomatch.com::%s: %s' % (testdonthdr, testdontval),
            '',
        ])
        write_file(testurlpatfile, txt)
        # expect to find only the header key (but not the literal filename) and only for the appropriate url
        expected = [mentionhdr % (testdohdr), mentionfile % (testcmdfile), mentionfile % (testurlpatfile)]
        not_expected = [testdoval, testdonthdr, testdontval]
        run_and_assert(args, "case E", expected, not_expected)

        # cleanup downloads
        shutil.rmtree(tmpdir)

    def test_test_report_env_filter(self):
        """Test use of --test-report-env-filter."""

        def toy(extra_args=None):
            """Build & install toy, return contents of test report."""
            eb_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
            args = [
                eb_file,
                '--force',
                '--debug',
            ]
            if extra_args is not None:
                args.extend(extra_args)
            with self.mocked_stdout_stderr():
                self.eb_main(args, do_build=True, raise_error=True, verbose=True)
            software_path = os.path.join(self.test_installpath, 'software', 'toy', '0.0')
            test_report_path_pattern = os.path.join(software_path, 'easybuild', 'easybuild-toy-0.0*test_report.md')
            test_report_txt = read_file(glob.glob(test_report_path_pattern)[0])
            return test_report_txt

        # define environment variables that should (not) show up in the test report
        # The name contains an auto-excluded pattern `SECRET`
        test_var_secret_always = 'THIS_IS_JUST_A_SECRET_ENV_VAR_FOR_EASYBUILD'
        os.environ[test_var_secret_always] = 'thisshouldremainsecretonrequest'
        # The name contains an autoexcluded value as a recognized GH token
        test_var_secret_always2 = 'THIS_IS_JUST_A_TOTALLY_PUBLIC_ENV_VAR_FOR_EASYBUILD'
        os.environ[test_var_secret_always2] = 'ghp_123456789_ABCDEFGHIJKlmnopqrstuvwxyz'
        # This should be in general present and excluded on demand
        test_var_secret_ondemand = 'THIS_IS_A_CUSTOM_ENV_VAR_FOR_EASYBUILD'
        os.environ[test_var_secret_ondemand] = 'thisshouldbehiddenondemand'
        test_var_public = 'THIS_IS_JUST_A_PUBLIC_ENV_VAR_FOR_EASYBUILD'
        os.environ[test_var_public] = 'thisshouldalwaysbeincluded'

        # default: no filtering
        test_report_txt = toy()
        self.assertIn(test_var_secret_ondemand, test_report_txt)
        self.assertIn(test_var_public, test_report_txt)
        self._assert_regexs([test_var_secret_always, test_var_secret_always2], test_report_txt, assert_true=False)

        # filter out env vars that match specified regex pattern
        filter_arg = "--test-report-env-filter=.*_IS_A_CUSTOM_ENV_VAR_FOR_EASYBUILD"
        test_report_txt = toy(extra_args=[filter_arg])
        regexs = [
            test_var_secret_ondemand,
            test_var_secret_always,
            test_var_secret_always2,
        ]
        self._assert_regexs(regexs, test_report_txt, assert_true=False)
        # make sure that used filter is reported correctly in test report
        filter_arg_regex = r"--test-report-env-filter='.\*_IS_A_CUSTOM_ENV_VAR_FOR_EASYBUILD'"
        self.assertRegex(test_report_txt, filter_arg_regex)

    def test_robot(self):
        """Test --robot and --robot-paths command line options."""
        # unset $EASYBUILD_ROBOT_PATHS that was defined in setUp
        os.environ['EASYBUILD_ROBOT_PATHS'] = self.test_prefix

        test_ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        # includes 'toy/.0.0-deps' as a dependency
        eb_file = os.path.join(test_ecs_path, 'g', 'gzip', 'gzip-1.4-GCC-4.6.3.eb')

        # hide test modules
        self.reset_modulepath([])

        # dependency resolution is disabled by default, even if required paths are available
        args = [
            eb_file,
            '--robot-paths=%s' % test_ecs_path,
        ]
        error_regex = r"Missing modules for dependencies .*: toy/\.0.0-deps"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_regex, self.eb_main, args, raise_error=True, do_build=True)

        # enable robot, but without passing path required to resolve toy dependency => FAIL
        # note that --dry-run is now robust against missing easyconfig, so shouldn't use it here
        args = [
            eb_file,
            '--robot',
        ]
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, 'Missing dependencies', self.eb_main, args, raise_error=True)

        # add path to test easyconfigs to robot paths, so dependencies can be resolved
        args.append('--dry-run')
        with self.mocked_stdout_stderr():
            self.eb_main(args + ['--robot-paths=%s' % test_ecs_path], raise_error=True)

        # copy test easyconfigs to easybuild/easyconfigs subdirectory of temp directory
        # to check whether easyconfigs install path is auto-included in robot path
        tmpdir = tempfile.mkdtemp(prefix='easybuild-easyconfigs-pkg-install-path')
        mkdir(os.path.join(tmpdir, 'easybuild'), parents=True)
        copy_dir(test_ecs_path, os.path.join(tmpdir, 'easybuild', 'easyconfigs'))

        # prepend path to test easyconfigs into Python search path, so it gets picked up as --robot-paths default
        del os.environ['EASYBUILD_ROBOT_PATHS']
        orig_sys_path = sys.path[:]
        sys.path.insert(0, tmpdir)
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True)

        shutil.rmtree(tmpdir)
        sys.path[:] = orig_sys_path

        # make sure that paths specified to --robot get preference over --robot-paths
        args = [
            eb_file,
            '--robot=%s' % test_ecs_path,
            '--robot-paths=%s' % os.path.join(tmpdir, 'easybuild', 'easyconfigs'),
            '--dry-run',
        ]
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True)

        ecfiles = [
            'g/GCC/GCC-4.6.3.eb',
            'i/intel/intel-2018a.eb',
            't/toy/toy-0.0-deps.eb',
            'g/gzip/gzip-1.4-GCC-4.6.3.eb',
        ]
        for ecfile in ecfiles:
            ec_regex = re.compile(r'^\s\*\s\[[xF ]\]\s%s' % os.path.join(test_ecs_path, ecfile), re.M)
            self.assertRegex(outtxt, ec_regex)

    def test_robot_path_check(self):
        """Test path check for --robot"""
        empty_file = os.path.join(self.test_prefix, 'empty')
        write_file(empty_file, '')

        error_pattern = "Argument passed to --robot is not an existing directory"
        for robot in ['--robot=foo', '--robot=%s' % empty_file]:
            args = ['toy-0.0.eb', '--dry-run', robot]
            with self.mocked_stdout_stderr():
                self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)

        toy_mod_txt = 'module: toy/0.0'

        # works fine is directory exists
        args = ['toy-0.0.eb', '-r', self.test_prefix, '--dry-run']
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True)
        self.assertIn(toy_mod_txt, outtxt)

        # no error when name of an easyconfig file is specified to --robot (even if it doesn't exist)
        args = ['--dry-run', '--robot', 'toy-0.0.eb']
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args, raise_error=True)
        self.assertIn(toy_mod_txt, outtxt)

        # different error when a non-existing easyconfig file is specified to --robot
        args = ['--dry-run', '--robot', 'no_such_easyconfig_file_in_robot_search_path.eb']
        error_pattern = "One or more files not found: no_such_easyconfig_file_in_robot_search_path.eb"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)

        for robot in ['-r%s' % self.test_prefix, '--robot=%s' % self.test_prefix]:
            args = ['toy-0.0.eb', '--dry-run', robot]
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, raise_error=True)
            self.assertIn(toy_mod_txt, outtxt)

        # no problem with using combos of single-letter options with -r included, no matter the order
        for arg in ['-Dr', '-rD', '-frkD', '-rfDk']:
            args = ['toy-0.0.eb', arg]
            with self.mocked_stdout_stderr():
                outtxt = self.eb_main(args, raise_error=True)
            self.assertIn(toy_mod_txt, outtxt)

        # unknown options are still recognized, even when used in single-letter combo arguments
        for arg in ['-DX', '-DrX', '-DXr', '-frkDX', '-XfrD']:
            args = ['toy-0.0.eb', arg]
            with self.mocked_stdout_stderr():
                self.assertErrorRegex(SystemExit, '.*', self.eb_main, args, raise_error=True, raise_systemexit=True)
                stderr = self.get_stderr()
            self.assertIn("error: no such option: -X", stderr)

    def test_missing_cfgfile(self):
        """Test behaviour when non-existing config file is specified."""
        args = ['--configfiles=/no/such/cfgfile.foo']
        error_regex = "parseconfigfiles: configfile .* not found"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_regex, self.eb_main, args, raise_error=True)

    def test_show_default_moduleclasses(self):
        """Test --show-default-moduleclasses."""
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        args = [
            '--unittest-file=%s' % self.logfile,
            '--show-default-moduleclasses',
        ]
        write_file(self.logfile, '')
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, verbose=True)
        logtxt = read_file(self.logfile)

        lst = ["\t%s:[ ]*%s" % (c, d.replace('(', '\\(').replace(')', '\\)')) for (c, d) in DEFAULT_MODULECLASSES]
        regex = re.compile("Default available module classes:\n\n" + '\n'.join(lst), re.M)

        self.assertRegex(logtxt, regex)

    def test_show_default_configfiles(self):
        """Test --show-default-configfiles."""
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        home = os.environ['HOME']
        for envvar in ['XDG_CONFIG_DIRS', 'XDG_CONFIG_HOME']:
            os.environ.pop(envvar, None)
        reload(easybuild.tools.options)

        args = [
            '--unittest-file=%s' % self.logfile,
            '--show-default-configfiles',
        ]

        cfgtxt = '\n'.join([
            '[config]',
            'prefix = %s' % self.test_prefix,
        ])

        expected_tmpl = '\n'.join([
            "Default list of configuration files:",
            '',
            "[with $XDG_CONFIG_HOME: %s, $XDG_CONFIG_DIRS: %s]",
            '',
            "* user-level: ${XDG_CONFIG_HOME:-$HOME/.config}/easybuild/config.cfg",
            "  -> %s",
            "* system-level: ${XDG_CONFIG_DIRS:-/etc/xdg}/easybuild.d/*.cfg",
            "  -> %s/easybuild.d/*.cfg => ",
        ])

        write_file(self.logfile, '')
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, verbose=True)
        logtxt = read_file(self.logfile)

        homecfgfile = os.path.join(os.environ['HOME'], '.config', 'easybuild', 'config.cfg')
        homecfgfile_str = homecfgfile
        if os.path.exists(homecfgfile):
            homecfgfile_str += " => found"
        else:
            homecfgfile_str += " => not found"
        expected = expected_tmpl % ('(not set)', '(not set)', homecfgfile_str, '{/etc/xdg}')
        self.assertIn(expected, logtxt)

        # to predict the full output, we need to take control over $HOME and $XDG_CONFIG_DIRS
        os.environ['HOME'] = self.test_prefix
        xdg_config_dirs = os.path.join(self.test_prefix, 'etc', 'xdg')
        os.environ['XDG_CONFIG_DIRS'] = xdg_config_dirs

        expected_tmpl += '\n'.join([
            "%s",
            '',
            "Default list of existing configuration files (%d, most important last):",
            "%s",
        ])

        # put dummy cfgfile in place in $HOME (to predict last line of output which only lists *existing* files)
        mkdir(os.path.join(self.test_prefix, '.config', 'easybuild'), parents=True)
        homecfgfile = os.path.join(self.test_prefix, '.config', 'easybuild', 'config.cfg')
        write_file(homecfgfile, cfgtxt)

        reload(easybuild.tools.options)
        write_file(self.logfile, '')
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, verbose=True)
        logtxt = read_file(self.logfile)
        expected = expected_tmpl % ('(not set)', xdg_config_dirs, "%s => found" % homecfgfile, '{%s}' % xdg_config_dirs,
                                    '(no matches)', 1, homecfgfile)
        self.assertIn(expected, logtxt)

        xdg_config_home = os.path.join(self.test_prefix, 'home')
        os.environ['XDG_CONFIG_HOME'] = xdg_config_home
        xdg_config_dirs = [os.path.join(self.test_prefix, 'moaretc'), os.path.join(self.test_prefix, 'etc', 'xdg')]
        os.environ['XDG_CONFIG_DIRS'] = os.pathsep.join(xdg_config_dirs)

        # put various dummy cfgfiles in place
        cfgfiles = [
            os.path.join(self.test_prefix, 'etc', 'xdg', 'easybuild.d', 'config.cfg'),
            os.path.join(self.test_prefix, 'moaretc', 'easybuild.d', 'bar.cfg'),
            os.path.join(self.test_prefix, 'moaretc', 'easybuild.d', 'foo.cfg'),
            os.path.join(xdg_config_home, 'easybuild', 'config.cfg'),
        ]
        for cfgfile in cfgfiles:
            mkdir(os.path.dirname(cfgfile), parents=True)
            write_file(cfgfile, cfgtxt)
        reload(easybuild.tools.options)

        write_file(self.logfile, '')
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, verbose=True)
        logtxt = read_file(self.logfile)
        expected = expected_tmpl % (xdg_config_home, os.pathsep.join(xdg_config_dirs),
                                    "%s => found" % os.path.join(xdg_config_home, 'easybuild', 'config.cfg'),
                                    '{' + ', '.join(xdg_config_dirs) + '}',
                                    ', '.join(cfgfiles[1:3]+[cfgfiles[0]]), 4, ', '.join(cfgfiles))
        self.assertIn(expected, logtxt)

        del os.environ['XDG_CONFIG_DIRS']
        del os.environ['XDG_CONFIG_HOME']
        os.environ['HOME'] = home
        reload(easybuild.tools.options)

    def test_generate_cmd_line(self):
        """Test for generate_cmd_line."""
        self.purge_environment()

        def generate_cmd_line(ebopts):
            """Helper function to filter generated command line (to ignore $EASYBUILD_IGNORECONFIGFILES)."""
            return [x for x in ebopts.generate_cmd_line() if not x.startswith('--ignoreconfigfiles=')]

        ebopts = EasyBuildOptions(envvar_prefix='EASYBUILD')
        self.assertEqual(generate_cmd_line(ebopts), [])

        ebopts = EasyBuildOptions(go_args=['--force'], envvar_prefix='EASYBUILD')
        self.assertEqual(generate_cmd_line(ebopts), ['--force'])

        ebopts = EasyBuildOptions(go_args=['--search=bar', '--search', 'foobar'], envvar_prefix='EASYBUILD')
        self.assertEqual(generate_cmd_line(ebopts), ["--search='foobar'"])

        os.environ['EASYBUILD_DEBUG'] = '1'
        ebopts = EasyBuildOptions(go_args=['--force'], envvar_prefix='EASYBUILD')
        self.assertEqual(generate_cmd_line(ebopts), ['--debug', '--force'])

        args = [
            # install path with a single quote in it, iieeeuuuwww
            "--installpath=/this/is/a/weird'prefix",
            '--test-report-env-filter=(COOKIE|SESSION)',
            '--suffix-modules-path=',
            '--try-toolchain=foss,2015b',
            '--logfile-format=easybuild,eb-%(name)s.log',
            # option with spaces with value wrapped in double quotes, oh boy...
            '--optarch="O3 -mtune=generic"',
        ]
        expected = [
            '--debug',
            "--installpath='/this/is/a/weird\\'prefix'",
            "--logfile-format='easybuild,eb-%(name)s.log'",
            "--optarch='O3 -mtune=generic'",
            "--suffix-modules-path=''",
            "--test-report-env-filter='(COOKIE|SESSION)'",
            "--try-toolchain='foss,2015b'",
        ]
        ebopts = EasyBuildOptions(go_args=args, envvar_prefix='EASYBUILD')
        self.assertEqual(generate_cmd_line(ebopts), expected)

    # must be run after test for --list-easyblocks, hence the '_xxx_'
    # cleaning up the imported easyblocks is quite difficult...
    def test_xxx_include_easyblocks(self):
        """Test --include-easyblocks."""
        orig_local_sys_path = sys.path[:]

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # clear log
        write_file(self.logfile, '')

        # existing test EB_foo easyblock found without include a custom one
        args = [
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)

        test_easyblocks = os.path.dirname(os.path.abspath(__file__))
        path_pattern = os.path.join(test_easyblocks, 'sandbox', 'easybuild', 'easyblocks', 'f', 'foo.py')
        foo_regex = re.compile(r"^\|-- EB_foo \(easybuild.easyblocks.foo @ %s\)" % path_pattern, re.M)
        self.assertRegex(logtxt, foo_regex)

        # 'undo' import of foo easyblock
        del sys.modules['easybuild.easyblocks.foo']
        sys.path[:] = orig_local_sys_path
        import easybuild.easyblocks
        reload(easybuild.easyblocks)
        import easybuild.easyblocks.generic
        reload(easybuild.easyblocks.generic)

        # kick out any paths that shouldn't be there for easybuild.easyblocks and easybuild.easyblocks.generic
        # to avoid that easyblocks picked up from other places cause trouble
        testdir_sandbox = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox')
        for pkg in ('easybuild.easyblocks', 'easybuild.easyblocks.generic'):
            for path in sys.modules[pkg].__path__[:]:
                if testdir_sandbox not in path:
                    sys.modules[pkg].__path__.remove(path)

        # include extra test easyblocks
        # Make them inherit from each other to trigger a known issue with changed imports, see #3779
        # Choose naming so that order of naming is different than inheritance order
        afoo_txt = textwrap.dedent("""
            from easybuild.framework.easyblock import EasyBlock
            class EB_afoo(EasyBlock):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
        """)
        write_file(os.path.join(self.test_prefix, 'afoo.py'), afoo_txt)
        foo_txt = textwrap.dedent("""
            from easybuild.easyblocks.zfoo import EB_zfoo
            class EB_foo(EB_zfoo):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
        """)
        write_file(os.path.join(self.test_prefix, 'foo.py'), foo_txt)
        zfoo_txt = textwrap.dedent("""
            from easybuild.easyblocks.afoo import EB_afoo
            class EB_zfoo(EB_afoo):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
        """)
        write_file(os.path.join(self.test_prefix, 'zfoo.py'), zfoo_txt)

        # clear log
        write_file(self.logfile, '')

        args = [
            '--include-easyblocks=%s/*.py' % self.test_prefix,
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)

        path_pattern = os.path.join(self.test_prefix, '.*', 'included-easyblocks-.*', 'easybuild', 'easyblocks',
                                    'foo.py')
        foo_regex = re.compile(r"^\|-- EB_foo \(easybuild.easyblocks.foo @ %s\)" % path_pattern, re.M)
        self.assertRegex(logtxt, foo_regex)

        ec_txt = '\n'.join([
            'easyblock = "EB_foo"',
            'name = "pi"',
            'version = "3.14"',
            'homepage = "http://example.com"',
            'description = "test easyconfig"',
            'toolchain = SYSTEM',
        ])
        ec = EasyConfig(path=None, rawtxt=ec_txt)

        # easyblock is found via get_easyblock_class
        for name in ('EB_afoo', 'EB_foo', 'EB_zfoo'):
            klass = get_easyblock_class(name)
            self.assertTrue(issubclass(klass, EasyBlock), "%s (%s) is an EasyBlock derivative class" % (klass, name))

            eb_inst = klass(ec)
            self.assertTrue(eb_inst is not None, "Instantiating the injected class %s works" % name)

        # 'undo' import of the easyblocks
        for name in ('afoo', 'foo', 'zfoo'):
            del sys.modules['easybuild.easyblocks.' + name]

    # must be run after test for --list-easyblocks, hence the '_xxx_'
    # cleaning up the imported easyblocks is quite difficult...
    def test_xxx_include_generic_easyblocks(self):
        """Test --include-easyblocks with a generic easyblock."""
        orig_local_sys_path = sys.path[:]
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # clear log
        write_file(self.logfile, '')

        # generic easyblock FooBar is not there initially
        error_msg = "Failed to obtain class for FooBar easyblock"
        self.assertErrorRegex(EasyBuildError, error_msg, get_easyblock_class, 'FooBar')

        # include extra test easyblocks
        txt = '\n'.join([
            'from easybuild.framework.easyblock import EasyBlock',
            'class FooBar(EasyBlock):',
            '   pass',
            ''
        ])
        write_file(os.path.join(self.test_prefix, 'generic', 'foobar.py'), txt)

        args = [
            '--include-easyblocks=%s/generic/*.py' % self.test_prefix,
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)

        path_pattern = os.path.join(self.test_prefix, '.*', 'included-easyblocks-.*', 'easybuild', 'easyblocks',
                                    'generic', 'foobar.py')
        foo_regex = re.compile(r"^\|-- FooBar \(easybuild.easyblocks.generic.foobar @ %s\)" % path_pattern, re.M)
        self.assertRegex(logtxt, foo_regex)

        klass = get_easyblock_class('FooBar')
        self.assertTrue(issubclass(klass, EasyBlock), "%s is an EasyBlock derivative class" % klass)

        # 'undo' import of foobar easyblock
        del sys.modules['easybuild.easyblocks.generic.foobar']
        os.remove(os.path.join(self.test_prefix, 'generic', 'foobar.py'))
        sys.path[:] = orig_local_sys_path
        import easybuild.easyblocks
        reload(easybuild.easyblocks)
        import easybuild.easyblocks.generic
        reload(easybuild.easyblocks.generic)

        # kick out any paths that shouldn't be there for easybuild.easyblocks and easybuild.easyblocks.generic
        # to avoid that easyblocks picked up from other places cause trouble
        testdir_sandbox = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox')
        for pkg in ('easybuild.easyblocks', 'easybuild.easyblocks.generic'):
            for path in sys.modules[pkg].__path__[:]:
                if testdir_sandbox not in path:
                    sys.modules[pkg].__path__.remove(path)

        error_msg = "Failed to obtain class for FooBar easyblock"
        self.assertErrorRegex(EasyBuildError, error_msg, get_easyblock_class, 'FooBar')

        # clear log
        write_file(self.logfile, '')

        # importing without specifying 'generic' also works, and generic easyblock can be imported as well
        # this works thanks to a fallback mechanism in get_easyblock_class
        txt = '\n'.join([
            'from easybuild.framework.easyblock import EasyBlock',
            'class GenericTest(EasyBlock):',
            '   pass',
            ''
        ])
        write_file(os.path.join(self.test_prefix, 'generictest.py'), txt)

        args[0] = '--include-easyblocks=%s/*.py' % self.test_prefix
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)

        mod_pattern = 'easybuild.easyblocks.generic.generictest'
        path_pattern = os.path.join(self.test_prefix, '.*', 'included-easyblocks-.*', 'easybuild', 'easyblocks',
                                    'generic', 'generictest.py')
        foo_regex = re.compile(r"^\|-- GenericTest \(%s @ %s\)" % (mod_pattern, path_pattern), re.M)
        self.assertRegex(logtxt, foo_regex)

        klass = get_easyblock_class('GenericTest')
        self.assertTrue(issubclass(klass, EasyBlock), "%s is an EasyBlock derivative class" % klass)

        # 'undo' import of foo easyblock
        del sys.modules['easybuild.easyblocks.generic.generictest']

    # must be run after test for --list-easyblocks, hence the '_xxx_'
    # cleaning up the imported easyblocks is quite difficult...
    def test_github_xxx_include_easyblocks_from_pr(self):
        """Test --include-easyblocks-from-pr."""
        if self.github_token is None:
            print("Skipping test_preview_pr, no GitHub token available?")
            return

        orig_local_sys_path = sys.path[:]

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # clear log
        write_file(self.logfile, '')

        # include extra test easyblock
        foo_txt = '\n'.join([
            'from easybuild.framework.easyblock import EasyBlock',
            'class EB_foo(EasyBlock):',
            '   pass',
            ''
        ])
        write_file(os.path.join(self.test_prefix, 'foo.py'), foo_txt)

        args = [
            '--include-easyblocks=%s/*.py' % self.test_prefix,  # this shouldn't interfere
            '--include-easyblocks-from-pr=3677',  # a PR for CMakeMake easyblock
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout()
        logtxt = read_file(self.logfile)

        self.assertFalse(stderr)
        self.assertEqual(stdout, "== easyblock cmakemake.py included from PR #3677\n")

        # easyblock included from pr is found
        path_pattern = os.path.join(self.test_prefix, '.*', 'included-easyblocks-.*', 'easybuild', 'easyblocks')
        cmm_pattern = os.path.join(path_pattern, 'generic', 'cmakemake.py')
        cmm_regex = re.compile(r"\|-- CMakeMake \(easybuild.easyblocks.generic.cmakemake @ %s\)" % cmm_pattern, re.M)
        self.assertRegex(logtxt, cmm_regex)

        # easyblock is found via get_easyblock_class
        klass = get_easyblock_class('CMakeMake')
        self.assertTrue(issubclass(klass, EasyBlock), "%s is an EasyBlock derivative class" % klass)

        # 'undo' import of easyblocks
        del sys.modules['easybuild.easyblocks.foo']
        del sys.modules['easybuild.easyblocks.generic.cmakemake']
        os.remove(os.path.join(self.test_prefix, 'foo.py'))
        sys.path[:] = orig_local_sys_path

        # include test cmakemake easyblock
        cmm_txt = '\n'.join([
            'from easybuild.framework.easyblock import EasyBlock',
            'class CMakeMake(EasyBlock):',
            '   pass',
            ''
        ])
        write_file(os.path.join(self.test_prefix, 'cmakemake.py'), cmm_txt)

        # including the same easyblock twice should work and give priority to the one from the PR
        args = [
            '--include-easyblocks=%s/*.py' % self.test_prefix,
            '--include-easyblocks-from-pr=3677',
            '--list-easyblocks=detailed',
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout()
        logtxt = read_file(self.logfile)

        expected = "WARNING: One or more easyblocks included from multiple locations: "
        expected += "cmakemake.py (the one(s) from PR #3677 will be used)"
        self.assertEqual(stderr.strip(), expected)
        self.assertEqual(stdout, "== easyblock cmakemake.py included from PR #3677\n")

        # easyblock included from pr is found
        path_pattern = os.path.join(self.test_prefix, '.*', 'included-easyblocks-.*', 'easybuild', 'easyblocks')
        cmm_pattern = os.path.join(path_pattern, 'generic', 'cmakemake.py')
        cmm_regex = re.compile(r"\|-- CMakeMake \(easybuild.easyblocks.generic.cmakemake @ %s\)" % cmm_pattern, re.M)
        self.assertRegex(logtxt, cmm_regex)

        # easyblock is found via get_easyblock_class
        klass = get_easyblock_class('CMakeMake')
        self.assertTrue(issubclass(klass, EasyBlock), "%s is an EasyBlock derivative class" % klass)

        # 'undo' import of easyblocks
        del sys.modules['easybuild.easyblocks.foo']
        del sys.modules['easybuild.easyblocks.generic.cmakemake']
        os.remove(os.path.join(self.test_prefix, 'cmakemake.py'))
        sys.path[:] = orig_local_sys_path
        import easybuild.easyblocks
        reload(easybuild.easyblocks)
        import easybuild.easyblocks.generic
        reload(easybuild.easyblocks.generic)

        # kick out any paths that shouldn't be there for easybuild.easyblocks and easybuild.easyblocks.generic,
        # to avoid that easyblocks picked up from other places cause trouble
        testdir_sandbox = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox')
        for pkg in ('easybuild.easyblocks', 'easybuild.easyblocks.generic'):
            for path in sys.modules[pkg].__path__[:]:
                if testdir_sandbox not in path:
                    sys.modules[pkg].__path__.remove(path)

        # clear log
        write_file(self.logfile, '')

        args = [
            '--from-pr=22589',  # PR for DIAMOND easyconfig
            '--include-easyblocks-from-pr=3677,3674',  # PRs for CMakeMake and LLVM easyblock
            '--unittest-file=%s' % self.logfile,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--extended-dry-run',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
            stderr, stdout = self.get_stderr(), self.get_stdout()
        logtxt = read_file(self.logfile)

        self.assertFalse(stderr)
        self.assertEqual(stdout, "== easyblock cmakemake.py included from PR #3677\n" +
                         "== easyblock llvm.py included from PR #3674\n")

        # easyconfig from pr is found
        ec_pattern = os.path.join(self.test_prefix, '.*', 'files_pr22589', 'd', 'DIAMOND',
                                  'DIAMOND-2.1.11-GCC-13.3.0.eb')
        ec_regex = re.compile(r"Parsing easyconfig file %s" % ec_pattern, re.M)
        self.assertRegex(logtxt, ec_regex)

        # easyblock included from pr is found

        self.assertIn("Derived full easyblock module path for CMakeMake: easybuild.easyblocks.generic.cmakemake",
                      logtxt)

        # easyblock is found via get_easyblock_class
        klass = get_easyblock_class('CMakeMake')
        self.assertTrue(issubclass(klass, EasyBlock), "%s is an EasyBlock derivative class" % klass)

        # 'undo' import of easyblocks
        del sys.modules['easybuild.easyblocks.llvm']
        del sys.modules['easybuild.easyblocks.generic.cmakemake']
        sys.path[:] = orig_local_sys_path
        import easybuild.easyblocks
        reload(easybuild.easyblocks)
        import easybuild.easyblocks.generic
        reload(easybuild.easyblocks.generic)

    def mk_eb_test_cmd(self, args):
        """Construct test command for 'eb' with given options."""

        # make sure that location to 'easybuild.main' is included in $PYTHONPATH
        pythonpath = os.getenv('PYTHONPATH')
        pythonpath = [pythonpath] if pythonpath else []
        easybuild_loc = os.path.dirname(os.path.dirname(easybuild.main.__file__))
        os.environ['PYTHONPATH'] = ':'.join([easybuild_loc] + pythonpath)

        return '; '.join([
            "cd %s" % self.test_prefix,
            "%s -O -m easybuild.main %s" % (sys.executable, ' '.join(args)),
        ])

    def test_include_module_naming_schemes(self):
        """Test --include-module-naming-schemes."""

        # make sure that calling out to 'eb' will work by restoring $PATH & $PYTHONPATH
        self.restore_env_path_pythonpath()

        topdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # try and make sure 'eb' is available via $PATH if it isn't yet
        path = self.env_path
        if which('eb') is None:
            path = '%s:%s' % (topdir, path)

        # try and make sure top-level directory is in $PYTHONPATH if it isn't yet
        pythonpath = self.env_pythonpath
        with self.mocked_stdout_stderr():
            res = run_shell_cmd("cd {self.test_prefix}; python -c 'import easybuild.framework'", fail_on_error=False)
        if res.exit_code != 0:
            pythonpath = '%s:%s' % (topdir, pythonpath)

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # clear log
        write_file(self.logfile, '')

        mns_regex = re.compile(r'^\s*TestIncludedMNS', re.M)

        # TestIncludedMNS module naming scheme is not available by default
        args = ['--avail-module-naming-schemes']
        test_cmd = self.mk_eb_test_cmd(args)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(test_cmd)
        self.assertNotRegex(res.output, mns_regex)

        # include extra test MNS
        mns_txt = '\n'.join([
            'from easybuild.tools.module_naming_scheme.mns import ModuleNamingScheme',
            'class TestIncludedMNS(ModuleNamingScheme):',
            '   pass',
        ])
        write_file(os.path.join(self.test_prefix, 'test_mns.py'), mns_txt)

        # clear log
        write_file(self.logfile, '')

        args.append('--include-module-naming-schemes=%s/*.py' % self.test_prefix)
        test_cmd = self.mk_eb_test_cmd(args)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(test_cmd)
        self.assertRegex(res.output, mns_regex)

    def test_use_included_module_naming_scheme(self):
        """Test using an included module naming scheme."""
        # try selecting the added module naming scheme
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # include extra test MNS
        mns_txt = '\n'.join([
            'import os',
            'from easybuild.tools.module_naming_scheme.mns import ModuleNamingScheme',
            'class AnotherTestIncludedMNS(ModuleNamingScheme):',
            '   def det_full_module_name(self, ec):',
            "       return os.path.join(ec['name'], ec['version'])",
        ])
        write_file(os.path.join(self.test_prefix, 'test_mns.py'), mns_txt)

        topdir = os.path.abspath(os.path.dirname(__file__))
        eb_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        args = [
            '--unittest-file=%s' % self.logfile,
            '--module-naming-scheme=AnotherTestIncludedMNS',
            '--force',
            eb_file,
        ]

        # selecting a module naming scheme that doesn't exist leads to 'invalid choice'
        error_regex = "Selected module naming scheme \'AnotherTestIncludedMNS\' is unknown"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_regex, self.eb_main, args, logfile=dummylogfn,
                                  raise_error=True, raise_systemexit=True)

        args.append('--include-module-naming-schemes=%s/*.py' % self.test_prefix)
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, do_build=True, raise_error=True, raise_systemexit=True, verbose=True)
        toy_mod = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            toy_mod += '.lua'
        self.assertExists(toy_mod)

    def test_include_toolchains(self):
        """Test --include-toolchains."""
        # make sure that calling out to 'eb' will work by restoring $PATH & $PYTHONPATH
        self.restore_env_path_pythonpath()

        topdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # try and make sure 'eb' is available via $PATH if it isn't yet
        path = self.env_path
        if which('eb') is None:
            path = '%s:%s' % (topdir, path)

        # try and make sure top-level directory is in $PYTHONPATH if it isn't yet
        pythonpath = self.env_pythonpath
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(f"cd {self.test_prefix}; python -c 'import easybuild.framework'", fail_on_error=False)
        if res.exit_code != 0:
            pythonpath = '%s:%s' % (topdir, pythonpath)

        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        # clear log
        write_file(self.logfile, '')

        # set processed attribute to false, to trigger rescan in search_toolchain
        setattr(easybuild.tools.toolchain, '%s_PROCESSED' % TC_CONST_PREFIX, False)

        tc_regex = re.compile(r'^\s*test_included_toolchain: TestIncludedCompiler', re.M)

        # TestIncludedCompiler is not available by default
        args = ['--list-toolchains']
        test_cmd = self.mk_eb_test_cmd(args)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(test_cmd)
        self.assertNotRegex(res.output, tc_regex)

        # include extra test toolchain
        comp_txt = '\n'.join([
            'from easybuild.tools.toolchain.compiler import Compiler',
            'class TestIncludedCompiler(Compiler):',
            "   COMPILER_MODULE_NAME = ['TestIncludedCompiler']",
        ])
        mkdir(os.path.join(self.test_prefix, 'compiler'))
        write_file(os.path.join(self.test_prefix, 'compiler', 'test_comp.py'), comp_txt)

        tc_txt = '\n'.join([
            'from easybuild.toolchains.compiler.test_comp import TestIncludedCompiler',
            'class TestIncludedToolchain(TestIncludedCompiler):',
            "   NAME = 'test_included_toolchain'",
        ])
        write_file(os.path.join(self.test_prefix, 'test_tc.py'), tc_txt)

        args.append('--include-toolchains=%s/*.py,%s/*/*.py' % (self.test_prefix, self.test_prefix))
        test_cmd = self.mk_eb_test_cmd(args)
        with self.mocked_stdout_stderr():
            res = run_shell_cmd(test_cmd)
        self.assertRegex(res.output, tc_regex)

    def test_cleanup_tmpdir(self):
        """Test --cleanup-tmpdir."""
        topdir = os.path.dirname(os.path.abspath(__file__))
        args = [
            os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb'),
            '--dry-run',
            '--try-software-version=1.0',  # so we get a tweaked easyconfig
        ]

        tmpdir = tempfile.gettempdir()
        # just making sure this is empty before we get started
        self.assertEqual(os.listdir(tmpdir), [])

        # force silence (since we're not using testing mode)
        with self.mocked_stdout_stderr(mock_stderr=False):
            # default: cleanup tmpdir & logfile
            self.eb_main(args, raise_error=True, testing=False)
            self.assertEqual(os.listdir(tmpdir), [])
            self.assertNotExists(self.logfile)

            # disable cleaning up tmpdir
            args.append('--disable-cleanup-tmpdir')
            self.eb_main(args, raise_error=True, testing=False)
            tmpdir_files = os.listdir(tmpdir)
            # tmpdir and logfile are still there \o/
            self.assertTrue(len(tmpdir_files) == 1)
            self.assertExists(self.logfile)
            # tweaked easyconfigs is still there \o/
            tweaked_dir = os.path.join(tmpdir, tmpdir_files[0], 'tweaked_easyconfigs')
            self.assertExists(os.path.join(tweaked_dir, 'toy-1.0.eb'))

    def test_github_preview_pr(self):
        """Test --preview-pr."""
        if self.github_token is None:
            print("Skipping test_preview_pr, no GitHub token available?")
            return

        test_ecs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        eb_file = os.path.join(test_ecs_path, 'b', 'bzip2', 'bzip2-1.0.6-GCC-4.9.2.eb')
        args = [
            '--color=never',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--preview-pr',
            eb_file,
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, raise_error=True)
            txt = self.get_stdout()
        self.assertRegex(txt, r"^Comparing bzip2-1.0.6\S* with bzip2-1.0.8")

    def test_github_review_pr(self):
        """Test --review-pr."""
        if self.github_token is None:
            print("Skipping test_review_pr, no GitHub token available?")
            return

        # PR for bwidget 1.10.1 easyconfig, see https://github.com/easybuilders/easybuild-easyconfigs/pull/22227
        args = [
            '--color=never',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--review-pr=22227',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True)
            txt = self.get_stdout()
        self.assertRegex(txt, r"^Comparing bwidget-1.10.1-\S* with bwidget-")

        # closed PR for gzip 1.2.8 easyconfig,
        # see https://github.com/easybuilders/easybuild-easyconfigs/pull/5365
        args = [
            '--color=never',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--review-pr=5365',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True, testing=True)
            txt = self.get_stdout()
        self.assertIn("This PR should be labelled with 'update'", txt)

        # test --review-pr-max
        args = [
            '--color=never',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--review-pr=5365',
            '--review-pr-max=1',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True, testing=True)
            txt = self.get_stdout()
        self.assertNotIn("2016.04", txt)

        # test --review-pr-filter
        args = [
            '--color=never',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--review-pr=5365',
            '--review-pr-filter=2016a',
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True, testing=True)
            txt = self.get_stdout()
        self.assertNotIn("2016.04", txt)

    def test_set_multiple_pr_opts(self):
        """Test that passing multiple PR options results in an error"""
        test_cases = [
            ['--new-pr', 'dummy.eb', '--preview-pr'],
            ['--new-pr', 'dummy.eb', '--update-pr', '42'],
            ['--new-pr', 'dummy.eb', '--sync-pr-with-develop', '42'],
            ['--new-pr', 'dummy.eb', '--new-pr-from-branch', 'mybranch'],
        ]
        for args in test_cases:
            error_pattern = "The following options are set but incompatible.* " + args[0]
            self.assertErrorRegex(EasyBuildError, error_pattern, self._run_mock_eb, args, raise_error=True)

    def test_set_tmpdir(self):
        """Test set_tmpdir config function."""
        self.purge_environment()

        def check_tmpdir(tmpdir):
            """Test use of specified path for temporary directory"""
            parent = tmpdir
            if parent is None:
                parent = tempfile.gettempdir()

            mytmpdir = set_tmpdir(tmpdir=tmpdir)

            parent = re.sub(r'[^\w/.-]', 'X', parent)

            for var in ['TMPDIR', 'TEMP', 'TMP']:
                self.assertTrue(os.environ[var].startswith(os.path.join(parent, 'eb-')))
                self.assertEqual(os.environ[var], mytmpdir)
            self.assertTrue(tempfile.gettempdir().startswith(os.path.join(parent, 'eb-')))
            tempfile_tmpdir = tempfile.mkdtemp()
            self.assertTrue(tempfile_tmpdir.startswith(os.path.join(parent, 'eb-')))
            fd, tempfile_tmpfile = tempfile.mkstemp()
            self.assertTrue(tempfile_tmpfile.startswith(os.path.join(parent, 'eb-')))

            # tmp_logdir follows tmpdir
            self.assertEqual(get_build_log_path(), mytmpdir)

            # cleanup
            os.close(fd)
            shutil.rmtree(mytmpdir)
            modify_env(os.environ, self.orig_environ)
            tempfile.tempdir = None

        orig_tmpdir = tempfile.gettempdir()
        cand_tmpdirs = [
            None,
            os.path.join(orig_tmpdir, 'foo'),
            os.path.join(orig_tmpdir, '[1234]. bleh'),
            os.path.join(orig_tmpdir, '[ab @cd]%/#*'),
        ]
        for tmpdir in cand_tmpdirs:
            check_tmpdir(tmpdir)

    def test_minimal_toolchains(self):
        """End-to-end test for --minimal-toolchains."""
        # create test easyconfig specifically tailored for this test
        # include a dependency for which no easyconfig is available with parent toolchains, only with subtoolchain
        ec_file = os.path.join(self.test_prefix, 'test_minimal_toolchains.eb')
        ectxt = '\n'.join([
            "easyblock = 'ConfigureMake'",
            "name = 'test'",
            "version = '1.2.3'",
            "homepage = 'http://example.com'",
            "description = 'this is just a test'",
            "toolchain = {'name': 'gompi', 'version': '2018a'}",
            # hwloc-1.11.8-gompi-2018a.eb is *not* available, but hwloc-1.11.8-GCC-6.4.0-2.28.eb is,
            # and GCC/6.4.0-2.28 is a subtoolchain of gompi/2018a
            "dependencies = [('hwloc', '1.11.8'), ('SQLite', '3.8.10.2')]",
        ])
        write_file(ec_file, ectxt)

        # check requirements for test
        init_config([], build_options={'robot_path': os.environ['EASYBUILD_ROBOT_PATHS']})
        self.assertNotExists(robot_find_easyconfig('hwloc', '1.11.8-gompi-2018a') or 'nosuchfile')
        self.assertExists(robot_find_easyconfig('hwloc', '1.11.8-GCC-6.4.0-2.28'))
        self.assertExists(robot_find_easyconfig('SQLite', '3.8.10.2-gompi-2018a'))
        self.assertExists(robot_find_easyconfig('SQLite', '3.8.10.2-GCC-6.4.0-2.28'))

        args = [
            ec_file,
            '--minimal-toolchains',
            '--module-naming-scheme=HierarchicalMNS',
            '--dry-run',
            '--robot',
        ]
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(args, do_build=True, raise_error=True, testing=False)
            txt = self.get_stdout()
        comp = 'Compiler/GCC/6.4.0-2.28'
        sqlite_regex = re.compile(r"SQLite-3.8.10.2-GCC-6.4.0-2.28.eb \(module: %s \| SQLite/" % comp, re.M)
        self.assertRegex(txt, sqlite_regex)

    def test_extended_dry_run(self):
        """Test use of --extended-dry-run/-x."""
        ec_file = os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        args = [
            ec_file,
            '--debug',
            '--disable-rpath',
        ]

        msg_regexs = [
            r"the actual build \& install procedure that will be performed may diverge",
            r"^\*\*\* DRY RUN using 'EB_toy' easyblock",
            r"^== COMPLETED: Installation ended successfully \(took .* secs?\)",
            r"^\(no ignored errors during dry run\)",
        ]
        ignoring_error_regex = r"WARNING: ignoring error"
        ignored_error_regex = r"WARNING: One or more errors were ignored, see warnings above"

        for opt in ['--extended-dry-run', '-x']:
            # check for expected patterns in output of --extended-dry-run/-x
            with self.mocked_stdout_stderr(mock_stderr=False):
                self.eb_main(args + [opt], do_build=True, raise_error=True, testing=False)
                stdout = self.get_stdout()

            self._assert_regexs(msg_regexs, stdout)

            # no ignored errors should occur
            self._assert_regexs([ignoring_error_regex, ignored_error_regex], stdout, assert_true=False)

    def test_last_log(self):
        """Test --last-log."""
        orig_tmpdir = os.environ['TMPDIR']
        tmpdir = os.path.join(tempfile.gettempdir(), 'eb-tmpdir1')
        current_log_path = os.path.join(tmpdir, 'easybuild-current.log')

        # $TMPDIR determines path to build log, we need to get it right to make the test check what we want it to
        os.environ['TMPDIR'] = tmpdir
        write_file(current_log_path, "this is a log message")
        self.assertEqual(find_last_log(current_log_path), None)
        os.environ['TMPDIR'] = orig_tmpdir

        mkdir(os.path.dirname(current_log_path))
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(['--last-log'], logfile=current_log_path, raise_error=True)
            txt = self.get_stdout().strip()

        self.assertEqual(txt, '(none)')

        # run something that fails first, we need a log file to find
        last_log_path = os.path.join(tempfile.gettempdir(), 'eb-tmpdir0', 'easybuild-last.log')
        mkdir(os.path.dirname(last_log_path))
        with self.mocked_stdout_stderr():
            self.eb_main(['thisisaneasyconfigthatdoesnotexist.eb'], logfile=last_log_path, raise_error=False)

        # $TMPDIR determines path to build log, we need to get it right to make the test check what we want it to
        os.environ['TMPDIR'] = tmpdir
        write_file(current_log_path, "this is a log message")
        last_log = find_last_log(current_log_path)
        self.assertTrue(os.path.samefile(last_log, last_log_path), "%s != %s" % (last_log, last_log_path))
        os.environ['TMPDIR'] = orig_tmpdir

        mkdir(os.path.dirname(current_log_path))
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.eb_main(['--last-log'], logfile=current_log_path, raise_error=True)
            txt = self.get_stdout().strip()

        self.assertTrue(os.path.samefile(txt, last_log_path), "%s != %s" % (txt, last_log_path))

    def test_fixed_installdir_naming_scheme(self):
        """Test use of --fixed-installdir-naming-scheme."""
        # by default, name of install dir match module naming scheme used
        topdir = os.path.abspath(os.path.dirname(__file__))
        eb_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        app = EasyBlock(EasyConfig(eb_file))
        app.gen_installdir()
        self.assertTrue(app.installdir.endswith('software/toy/0.0'))

        init_config(args=['--module-naming-scheme=HierarchicalMNS'])
        app = EasyBlock(EasyConfig(eb_file))
        app.gen_installdir()
        self.assertTrue(app.installdir.endswith('software/toy/0.0'))

        # with --fixed-installdir-naming-scheme, the EasyBuild naming scheme is used
        build_options = {
            'fixed_installdir_naming_scheme': False,
            'valid_module_classes': module_classes(),
        }
        init_config(args=['--module-naming-scheme=HierarchicalMNS'], build_options=build_options)
        app = EasyBlock(EasyConfig(eb_file))
        app.gen_installdir()
        self.assertTrue(app.installdir.endswith('software/Core/toy/0.0'))

    def _assert_regexs(self, regexs, txt, assert_true=True):
        """Helper function to assert presence/absence of list of regex patterns in a text"""
        for regex in regexs:
            regex = re.compile(regex, re.M)
            if assert_true:
                self.assertRegex(txt, regex)
            else:
                self.assertNotRegex(txt, regex)

    def _run_mock_eb(self, args, strip=False, **kwargs):
        """Helper function to mock easybuild runs

        Return (stdout, stderr) optionally stripped of whitespace at start/end
        """
        with self.mocked_stdout_stderr() as (stdout, stderr):
            self.eb_main(args, **kwargs)
        stdout_txt = stdout.getvalue()
        stderr_txt = stderr.getvalue()
        if strip:
            stdout_txt = stdout_txt.strip()
            stderr_txt = stderr_txt.strip()
        return stdout_txt, stderr_txt

    def test_new_branch_github(self):
        """Test for --new-branch-github."""
        if self.github_token is None:
            print("Skipping test_create_branch_github, no GitHub token available?")
            return

        topdir = os.path.dirname(os.path.abspath(__file__))

        # test easyconfigs
        test_ecs = os.path.join(topdir, 'easyconfigs', 'test_ecs')
        toy_ec = os.path.join(test_ecs, 't', 'toy', 'toy-0.0.eb')

        args = [
            '--new-branch-github',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_ec,
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        remote = 'git@github.com:%s/easybuild-easyconfigs.git' % GITHUB_TEST_ACCOUNT
        regexs = [
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-easyconfigs.git\.\.\.",
            r"^== copying files to .*/easybuild-easyconfigs\.\.\.",
            r"^== pushing branch '[0-9]{14}_new_pr_toy00' to remote '.*' \(%s\) \[DRY RUN\]" % remote,
        ]
        self._assert_regexs(regexs, txt)

        # test easyblocks
        test_ebs = os.path.join(topdir, 'sandbox', 'easybuild', 'easyblocks')
        toy_eb = os.path.join(test_ebs, 't', 'toy.py')

        args = [
            '--new-branch-github',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_eb,
            '--pr-title="add easyblock for toy"',
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        remote = 'git@github.com:%s/easybuild-easyblocks.git' % GITHUB_TEST_ACCOUNT
        regexs = [
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-easyblocks.git\.\.\.",
            r"^== copying files to .*/easybuild-easyblocks\.\.\.",
            r"^== pushing branch '[0-9]{14}_new_pr_toy' to remote '.*' \(%s\) \[DRY RUN\]" % remote,
        ]
        self._assert_regexs(regexs, txt)

        # test framework with tweaked copy of test_module_naming_scheme.py
        test_mns_py = os.path.join(topdir, 'sandbox', 'easybuild', 'tools', 'module_naming_scheme',
                                   'test_module_naming_scheme.py')
        target_dir = os.path.join(self.test_prefix, 'easybuild-framework', 'test', 'framework', 'sandbox',
                                  'easybuild', 'tools', 'module_naming_scheme')
        mkdir(target_dir, parents=True)
        copy_file(test_mns_py, target_dir)
        test_mns_py = os.path.join(target_dir, os.path.basename(test_mns_py))
        write_file(test_mns_py, '\n\n', append=True)

        args = [
            '--new-branch-github',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            test_mns_py,
            '--pr-commit-msg="a test"',
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        remote = 'git@github.com:%s/easybuild-framework.git' % GITHUB_TEST_ACCOUNT
        regexs = [
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-framework.git\.\.\.",
            r"^== copying files to .*/easybuild-framework\.\.\.",
            r"^== pushing branch '[0-9]{14}_new_pr_[A-Za-z]{10}' to remote '.*' \(%s\) \[DRY RUN\]" % remote,
        ]
        self._assert_regexs(regexs, txt)

    def test_github_new_pr_from_branch(self):
        """Test --new-pr-from-branch."""
        if self.github_token is None:
            print("Skipping test_github_new_pr_from_branch, no GitHub token available?")
            return

        # see https://github.com/boegel/easybuild-easyconfigs/tree/test_new_pr_from_branch_DO_NOT_REMOVE
        # branch created specifically for this test,
        # only adds toy-0.0.eb test easyconfig compared to central develop branch
        test_branch = 'test_new_pr_from_branch_DO_NOT_REMOVE'

        args = [
            '--new-pr-from-branch=%s' % test_branch,
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,  # used to get GitHub token
            '--github-org=boegel',  # used to determine account to grab branch from
            '--pr-descr="an easyconfig for toy"',
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs = [
            r"^== fetching branch '%s' from https://github.com/boegel/easybuild-easyconfigs.git\.\.\." % test_branch,
            r"^== syncing 'test_new_pr_from_branch_DO_NOT_REMOVE' with current 'easybuilders/develop' branch\.\.\.",
            r"^== pulling latest version of 'develop' branch from easybuilders/easybuild-easyconfigs\.\.\.",
            r"^== merging 'develop' branch into PR branch 'test_new_pr_from_branch_DO_NOT_REMOVE'\.\.\.",
            r"^== checking out target branch 'easybuilders/develop'\.\.\.",
            r"^== determining metadata for pull request based on changed files\.\.\.",
            r"^== found 1 changed file\(s\) in 'boegel/test_new_pr_from_branch_DO_NOT_REMOVE' " +
            "relative to 'easybuilders/develop':$",
            r"^\* 1 new/changed easyconfig file\(s\):\n  easybuild/easyconfigs/t/toy/toy-0\.0\.eb",
            r"^== checking out PR branch 'boegel/test_new_pr_from_branch_DO_NOT_REMOVE'\.\.\.$",
            r"\* target: easybuilders/easybuild-easyconfigs:develop$",
            r"^\* from: boegel/easybuild-easyconfigs:test_new_pr_from_branch_DO_NOT_REMOVE$",
            r'^\* title: "\{tools\}\[system/system\] toy v0\.0"$',
            r'^"an easyconfig for toy"$',
            r"^ 1 file changed, [0-9]+ insertions\(\+\)$",
            r"^\* overview of changes:\n  easybuild/easyconfigs/t/toy/toy-0\.0\.eb | [0-9]+",
        ]
        self._assert_regexs(regexs, txt)

    def test_update_branch_github(self):
        """Test --update-branch-github."""
        if self.github_token is None:
            print("Skipping test_update_branch_github, no GitHub token available?")
            return

        topdir = os.path.dirname(os.path.abspath(__file__))
        test_ecs = os.path.join(topdir, 'easyconfigs', 'test_ecs')
        toy_ec = os.path.join(test_ecs, 't', 'toy', 'toy-0.0.eb')

        args = [
            '--update-branch-github=develop',
            '--github-user=boegel',  # used to determine account to grab branch from (no GitHub token needed)
            toy_ec,
            '--pr-commit-msg="this is just a test"',
            '--force',  # force required because we're using --pr-commit-msg when only adding new easyconfigs
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        full_repo = 'boegel/easybuild-easyconfigs'
        regexs = [
            r"^== fetching branch 'develop' from https://github.com/%s.git\.\.\." % full_repo,
            r"^== copying files to .*/git-working-dir.*/easybuild-easyconfigs...",
            r"^== pushing branch 'develop' to remote '.*' \(git@github.com:%s.git\) \[DRY RUN\]" % full_repo,
            r"^Overview of changes:\n.*/easyconfigs/t/toy/toy-0.0.eb \| [0-9]+",
            r"== pushed updated branch 'develop' to boegel/easybuild-easyconfigs \[DRY RUN\]",
        ]
        self._assert_regexs(regexs, txt)

    def test_github_new_update_pr(self):
        """Test use of --new-pr (dry run only)."""
        if self.github_token is None:
            print("Skipping test_new_update_pr, no GitHub token available?")
            return

        # copy toy test easyconfig
        topdir = os.path.dirname(os.path.abspath(__file__))
        test_ecs = os.path.join(topdir, 'easyconfigs', 'test_ecs')
        toy_ec = os.path.join(self.test_prefix, 'toy.eb')
        toy_patch_fn = 'toy-0.0_fix-silly-typo-in-printf-statement.patch'
        toy_patch = os.path.join(topdir, 'sandbox', 'sources', 'toy', toy_patch_fn)
        # purposely picked one with non-default toolchain/versionsuffix
        copy_file(os.path.join(test_ecs, 't', 'toy', 'toy-0.0-gompi-2018a-test.eb'), toy_ec)

        # modify file to mock archived easyconfig
        toy_ec_txt = read_file(toy_ec)
        toy_ec_txt = '\n'.join([
            "# Built with EasyBuild version 3.1.2 on 2017-04-25_21-35-15",
            toy_ec_txt,
            "# Build statistics",
            "buildstats = [{",
            '   "build_time": 8.34,',
            '   "os_type": "Linux",',
            "}]",
        ])
        write_file(toy_ec, toy_ec_txt)

        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_ec,
            '-D',
            '--disable-cleanup-tmpdir',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        # determine location of repo clone, can be used to test --git-working-dirs-path (and save time)
        dirs = glob.glob(os.path.join(self.test_prefix, 'eb-*', '*', 'git-working-dir*'))
        if len(dirs) == 1:
            git_working_dir = dirs[0]
        else:
            self.fail("Failed to find temporary git working dir: %s" % dirs)
        args.append(f'--git-working-dirs-path={git_working_dir}')

        remote = 'git@github.com:%s/easybuild-easyconfigs.git' % GITHUB_TEST_ACCOUNT
        regexs = [
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-easyconfigs.git...",
            r"^== pushing branch '.*' to remote '.*' \(%s\)" % remote,
            r"^Opening pull request \[DRY RUN\]",
            r"^\* target: easybuilders/easybuild-easyconfigs:develop",
            r"^\* from: %s/easybuild-easyconfigs:.*_new_pr_toy00" % GITHUB_TEST_ACCOUNT,
            r"^\* title: \"\{tools\}\[gompi/2018a\] toy v0.0 w/ test\"",
            r"\(created using `eb --new-pr`\)",  # description
            r"^\* overview of changes:",
            r".*/toy-0.0-gompi-2018a-test.eb\s*\|",
            r"^\s*1 file(s?) changed",
        ]
        self._assert_regexs(regexs, txt)

        # Commit message must not be specified for only new ECs
        args_new_pr = args + ['--pr-commit-msg=just a test']
        error_msg = r"PR commit msg \(--pr-commit-msg\) should not be used"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args_new_pr, raise_error=True, testing=False)

        # But commit message can still be specified when using --force
        args_new_pr.append('--force')
        txt, _ = self._run_mock_eb(args_new_pr, do_build=True, raise_error=True, testing=False)
        regexs_with_msg = [
            error_msg,  # Still shown as a warning
            r'== Using the specified --pr-commit-msg',
            r'\* title: "just a test"',
        ]
        self._assert_regexs(regexs_with_msg, txt)

        # add unstaged file to git working dir, to check on later
        unstaged_file = os.path.join('easybuild-easyconfigs', 'easybuild', 'easyconfigs', 'test.eb')
        write_file(os.path.join(git_working_dir, unstaged_file), 'test123')
        # Remove other temporary git working dirs
        res = glob.glob(os.path.join(self.test_prefix, 'eb-*', 'eb-*', 'git-working-dir*'))
        res = [d for d in res if d != git_working_dir]
        for path in res:
            remove_dir(path)

        ec_name = 'bzip2-1.0.8.eb'
        # a custom commit message is required when doing more than just adding new easyconfigs (e.g., deleting a file)
        args.append(f':{ec_name}')
        error_msg = f"A meaningful commit message must be specified via --pr-commit-msg.*\nDeleted: {ec_name}"

        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True, testing=False)

        # check whether unstaged file in git working dir was copied (it shouldn't)
        res = glob.glob(os.path.join(self.test_prefix, 'eb-*', 'eb-*', 'git-working-dir*'))
        res = [d for d in res if d != git_working_dir]
        if len(res) == 1:
            unstaged_file_full = os.path.join(res[0], unstaged_file)
            self.assertNotExists(unstaged_file_full)
        else:
            self.fail("Found copy of easybuild-easyconfigs working copy")

        # add required commit message, try again
        args.append('--pr-commit-msg=just a test')
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs[-1] = r"^\s*2 files changed"
        regexs.remove(r"^\* title: \"\{tools\}\[gompi/2018a\] toy v0.0 w/ test\"")
        regexs.append(r"^\* title: \"just a test\"")
        regexs.append(rf".*/{ec_name}\s*\|")
        regexs.append(r".*[0-9]+ deletions\(-\)")
        self._assert_regexs(regexs, txt)

        GITHUB_TEST_ORG = 'test-organization'
        args.extend([
            '--pr-branch-name=branch_name_for_new_pr_test',
            '--pr-commit-msg="this is a commit message. really!"',
            '--pr-descr="moar letters foar teh lettre box"',
            '--pr-target-branch=main',
            '--github-org=%s' % GITHUB_TEST_ORG,
            '--pr-target-account=boegel',  # we need to be able to 'clone' from here (via https)
            '--pr-title=test-1-2-3',
        ])
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs = [
            r"^== fetching branch 'main' from https://github.com/boegel/easybuild-easyconfigs.git...",
            r"^Opening pull request \[DRY RUN\]",
            r"^\* target: boegel/easybuild-easyconfigs:main",
            r"^\* from: %s/easybuild-easyconfigs:branch_name_for_new_pr_test" % GITHUB_TEST_ORG,
            r"\(created using `eb --new-pr`\)",  # description
            r"moar letters foar teh lettre box",  # also description (see --pr-descr)
            r"^\* title: \"test-1-2-3\"",
            r"^\* overview of changes:",
            r".*/toy-0.0-gompi-2018a-test.eb\s*\|",
            rf".*/{ec_name}\s*\|",
            r"^\s*2 files changed",
            r".*[0-9]+ deletions\(-\)",
        ]
        self._assert_regexs(regexs, txt)

        # should also work with a patch
        args.append(toy_patch)
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True, testing=False)
            txt = self.get_stdout()

        regexs[-2] = r"^\s*3 files changed"
        regexs.append(r".*_fix-silly-typo-in-printf-statement.patch\s*\|")
        self._assert_regexs(regexs, txt)

        # modifying an existing easyconfig requires a custom PR title;
        # we need to use a sufficiently recent GCC version, since easyconfigs for old versions have been archived
        gcc_ec = os.path.join(test_ecs, 'g', 'GCC', 'GCC-10.2.0.eb')
        gcc_new_ec = os.path.join(self.test_prefix, 'GCC-14.3.0.eb')
        gcc_new_txt = read_file(gcc_ec).replace('10.2.0', '14.3.0')
        write_file(gcc_new_ec, gcc_new_txt)

        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_ec,
            gcc_new_ec,
            '-D',
        ]
        error_msg = "A meaningful commit message must be specified via --pr-commit-msg.*\n"
        error_msg += "Modified: " + os.path.basename(gcc_new_ec)
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        # also specifying commit message is sufficient; PR title is inherited from commit message
        args.append('--pr-commit-msg=this is just a test')
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regex = re.compile(r'^\* title: "this is just a test"', re.M)
        self.assertRegex(txt, regex)

        args = [
            # PR for EasyBuild v2.5.0 release
            # we need a PR where the base branch is still available ('develop', in this case)
            '--update-pr=2237',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_ec,
            '-D',
            # only to speed things up
            '--git-working-dirs-path=%s' % git_working_dir,
        ]

        error_msg = "A meaningful commit message must be specified via --pr-commit-msg when using --update-pr"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        args.append('--pr-commit-msg=just a test')
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs = [
            r"^== Determined branch name corresponding to easybuilders/easybuild-easyconfigs PR #2237: develop",
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-easyconfigs.git...",
            r".*/toy-0.0-gompi-2018a-test.eb\s*\|",
            r"^\s*1 file(s?) changed",
            r"^== pushing branch 'develop' to remote '.*' \(git@github.com:easybuilders/easybuild-easyconfigs.git\)",
            r"^== pushed updated branch 'develop' to easybuilders/easybuild-easyconfigs \[DRY RUN\]",
            r"^== updated https://github.com/easybuilders/easybuild-easyconfigs/pull/2237 \[DRY RUN\]",
        ]
        self._assert_regexs(regexs, txt)

        # also check behaviour under --extended-dry-run/-x
        args.remove('-D')
        args.append('-x')

        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs.extend([
            r"Full patch:",
            r"^\+\+\+\s*.*toy-0.0-gompi-2018a-test.eb",
            r"^\+name = 'toy'",
        ])
        self._assert_regexs(regexs, txt)

        # check whether comments/buildstats get filtered out
        regexs = [
            r"# Built with EasyBuild",
            r"# Build statistics",
            r"buildstats\s*=",
        ]
        self._assert_regexs(regexs, txt, assert_true=False)

    def test_github_new_pr_warning_missing_patch(self):
        """Test warning printed by --new-pr (dry run only) when a specified patch file could not be found."""

        if self.github_token is None:
            print("Skipping test_new_pr_warning_missing_patch, no GitHub token available?")
            return

        topdir = os.path.dirname(os.path.abspath(__file__))
        test_ecs = os.path.join(topdir, 'easyconfigs', 'test_ecs')
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        copy_file(os.path.join(test_ecs, 't', 'toy', 'toy-0.0-gompi-2018a-test.eb'), test_ec)

        patches_regex = re.compile(r'^patches = .*', re.M)
        test_ec_txt = read_file(test_ec)

        patch_fn = 'this_patch_does_not_exist.patch'
        test_ec_txt = patches_regex.sub('patches = ["%s"]' % patch_fn, test_ec_txt)
        write_file(test_ec, test_ec_txt)

        new_pr_out_regex = re.compile(r"Opening pull request", re.M)
        warning_regex = re.compile("new patch file %s, referenced by .*, is not included in this PR" % patch_fn, re.M)

        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            test_ec,
            '-D',
        ]
        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        self.assertRegex(stdout, new_pr_out_regex)
        self.assertRegex(stderr, warning_regex)

        # try again with patch specified via a dict value
        test_ec_txt = patches_regex.sub('patches = [{"name": "%s", "alt_location": "foo"}]' % patch_fn, test_ec_txt)
        write_file(test_ec, test_ec_txt)

        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        self.assertRegex(stdout, new_pr_out_regex)
        self.assertRegex(stderr, warning_regex)

    def test_github_sync_pr_with_develop(self):
        """Test use of --sync-pr-with-develop (dry run only)."""
        if self.github_token is None:
            print("Skipping test_sync_pr_with_develop, no GitHub token available?")
            return

        # use https://github.com/easybuilders/easybuild-easyconfigs/pull/9150,
        # which is a PR from boegel:develop to easybuilders:develop
        # (to sync 'develop' branch in boegel's fork with central develop branch);
        # we need to test with a branch that is guaranteed to stay in place for the test to work,
        # since it will actually be downloaded (only the final push to update the branch is skipped under --dry-run)
        args = [
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--sync-pr-with-develop=9150',
            '--dry-run',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        github_path = r"boegel/easybuild-easyconfigs\.git"
        pattern = '\n'.join([
            r"== Temporary log file in case of crash .*",
            r"== Determined branch name corresponding to easybuilders/easybuild-easyconfigs PR #9150: develop",
            r"== fetching branch 'develop' from https://github\.com/%s\.\.\." % github_path,
            r"== pulling latest version of 'develop' branch from easybuilders/easybuild-easyconfigs\.\.\.",
            r"== merging 'develop' branch into PR branch 'develop'\.\.\.",
            r"== pushing branch 'develop' to remote '.*' \(git@github\.com:%s\) \[DRY RUN\]" % github_path,
        ])
        self.assertTrue(re.match(pattern, txt), "Pattern '%s' doesn't match: %s" % (pattern, txt))

    def test_github_sync_branch_with_develop(self):
        """Test use of --sync-branch-with-develop (dry run only)."""
        if self.github_token is None:
            print("Skipping test_sync_pr_with_develop, no GitHub token available?")
            return

        # see https://github.com/boegel/easybuild-easyconfigs/tree/test_new_pr_from_branch_DO_NOT_REMOVE
        test_branch = 'test_new_pr_from_branch_DO_NOT_REMOVE'

        args = [
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--github-org=boegel',  # used to determine account to grab branch from
            '--sync-branch-with-develop=%s' % test_branch,
            '--dry-run',
        ]
        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        self.assertFalse(stderr)

        github_path = r"boegel/easybuild-easyconfigs\.git"
        pattern = '\n'.join([
            r"== Temporary log file in case of crash .*",
            r"== fetching branch '%s' from https://github\.com/%s\.\.\." % (test_branch, github_path),
            r"== pulling latest version of 'develop' branch from easybuilders/easybuild-easyconfigs\.\.\.",
            r"== merging 'develop' branch into PR branch '%s'\.\.\." % test_branch,
            r"== pushing branch '%s' to remote '.*' \(git@github\.com:%s\) \[DRY RUN\]" % (test_branch, github_path),
        ])
        self.assertTrue(re.match(pattern, stdout), "Pattern '%s' doesn't match: %s" % (pattern, stdout))

    def test_github_new_pr_python(self):
        """Check generated PR title for --new-pr on easyconfig that includes Python dependency."""
        if self.github_token is None:
            print("Skipping test_new_pr_python, no GitHub token available?")
            return

        # copy toy test easyconfig
        test_ecs = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        toy_ec = os.path.join(self.test_prefix, 'toy.eb')
        copy_file(os.path.join(test_ecs, 't', 'toy', 'toy-0.0.eb'), toy_ec)

        # modify file to include Python dependency
        toy_ec_txt = read_file(toy_ec)
        write_file(toy_ec, toy_ec_txt + "\ndependencies = [('Python', '3.7.2')]")

        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            toy_ec,
            '-D',
            '--disable-cleanup-tmpdir',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regex = re.compile(r"^\* title: \"\{tools\}\[system/system\] toy v0.0\"$", re.M)
        self.assertRegex(txt, regex)

        # if multiple easyconfigs depending on Python are included, Python version is only listed once
        gzip_ec = os.path.join(self.test_prefix, 'test.eb')
        copy_file(os.path.join(test_ecs, 'g', 'gzip', 'gzip-1.4.eb'), gzip_ec)
        gzip_ec_txt = read_file(gzip_ec)
        write_file(gzip_ec, gzip_ec_txt + "\ndependencies = [('Python', '3.7.2')]")

        txt, _ = self._run_mock_eb(args + [gzip_ec], do_build=True, raise_error=True, testing=False)

        regex = re.compile(r"^\* title: \"\{tools\}\[system/system\] toy v0.0, gzip v1.4\"$", re.M)
        self.assertRegex(txt, regex)

        # also check with Python listed via multi_deps
        write_file(toy_ec, toy_ec_txt + "\nmulti_deps = {'Python': ['3.7.2', '2.7.15']}")
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regex = re.compile(r"^\* title: \"\{tools\}\[system/system\] toy v0.0\"$", re.M)
        self.assertRegex(txt, regex)

    def test_github_new_pr_delete(self):
        """Test use of --new-pr to delete easyconfigs."""

        if self.github_token is None:
            print("Skipping test_new_pr_delete, no GitHub token available?")
            return

        ec_name = 'bzip2-1.0.8.eb'
        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            f':{ec_name}',
            '-D',
            '--disable-cleanup-tmpdir',
            f'--pr-title=delete {ec_name}',
            f'--pr-commit-msg="delete {ec_name}"'
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs = [
            r"^== fetching branch 'develop' from https://github.com/easybuilders/easybuild-easyconfigs.git...",
            rf'title: "delete {ec_name}"',
            r"1 file(s?) changed,( 0 insertions\(\+\),)? [0-9]+ deletions\(-\)",
        ]
        self._assert_regexs(regexs, txt)

    def test_github_new_pr_dependencies(self):
        """Test use of --new-pr with automatic dependency lookup."""

        if self.github_token is None:
            print("Skipping test_new_pr_dependencies, no GitHub token available?")
            return

        foo_eb = '\n'.join([
            'easyblock = "ConfigureMake"',
            'name = "foo"',
            'version = "1.0"',
            'homepage = "http://example.com"',
            'description = "test easyconfig"',
            'toolchain = SYSTEM',
            'dependencies = [("bar", "2.0")]'
        ])
        bar_eb = '\n'.join([
            'easyblock = "ConfigureMake"',
            'name = "bar"',
            'version = "2.0"',
            'homepage = "http://example.com"',
            'description = "test easyconfig"',
            'toolchain = SYSTEM',
        ])

        write_file(os.path.join(self.test_prefix, 'foo-1.0.eb'), foo_eb)
        write_file(os.path.join(self.test_prefix, 'bar-2.0.eb'), bar_eb)

        args = [
            '--new-pr',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            os.path.join(self.test_prefix, 'foo-1.0.eb'),
            '-D',
            '--disable-cleanup-tmpdir',
            '-r%s' % self.test_prefix,
        ]

        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        regexs = [
            r"^\* overview of changes:",
            r".*/foo-1\.0\.eb\s*\|",
            r".*/bar-2\.0\.eb\s*\|",
            r"^\s*2 files changed",
        ]

        self._assert_regexs(regexs, txt)

    def test_github_new_pr_easyblock(self):
        """
        Test using --new-pr to open an easyblocks PR
        """

        if self.github_token is None:
            print("Skipping test_new_pr_easyblock, no GitHub token available?")
            return

        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_eb = os.path.join(topdir, 'sandbox', 'easybuild', 'easyblocks', 't', 'toy.py')
        self.assertExists(toy_eb)

        args = [
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--new-pr',
            toy_eb,
            '-D',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        patterns = [
            r'target: easybuilders/easybuild-easyblocks:develop',
            r'from: easybuild_test/easybuild-easyblocks:[0-9]+_new_pr_toy',
            r'title: "new easyblock for toy"',
            r'easybuild/easyblocks/t/toy.py',
        ]
        self._assert_regexs(patterns, txt)

    def test_github_merge_pr(self):
        """
        Test use of --merge-pr (dry run)"""
        if self.github_token is None:
            print("Skipping test_merge_pr, no GitHub token available?")
            return

        # start by making sure --merge-pr without dry-run errors out for a closed PR
        args = [
            '--merge-pr',
            '11753',  # closed PR
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
        ]
        error_msg = r"This PR is closed."
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        # and also for an already merged PR
        args = [
            '--merge-pr',
            '11769',  # already merged PR
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
        ]
        error_msg = r"This PR is already merged."
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        # merged PR for EasyBuild-3.3.0.eb, is missing approved review
        args = [
            '--merge-pr',
            '4781',  # PR for easyconfig for EasyBuild-3.3.0.eb
            '-D',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--pr-target-branch=some_branch',
        ]

        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        expected_stdout = '\n'.join([
            "Checking eligibility of easybuilders/easybuild-easyconfigs PR #4781 for merging...",
            "* last test report is successful: OK",
            "* no pending change requests: OK",
            "* milestone is set: OK (3.3.1)",
            "* mergeable state is clean: PR is already merged",
        ])
        expected_stderr = '\n'.join([
            "* targets some_branch branch: FAILED; found 'develop' => not eligible for merging!",
            # since commit status for old PRs is no longer available, so test suite check fails
            "* test suite passes: (status: None) => not eligible for merging!",
            "* approved review: MISSING => not eligible for merging!",
            '',
            "WARNING: Review indicates this PR should not be merged (use -f/--force to do so anyway)",
        ])
        self.assertEqual(stderr.strip(), expected_stderr)
        self.assertTrue(stdout.strip().endswith(expected_stdout), "'%s' ends with '%s'" % (stdout, expected_stdout))

        # full eligible merged PR, default target branch;
        # note: we frequently need to change to a more recent PR here,
        #       to avoid that this test starts failing because commit status is set to None for old commits
        del args[-1]
        # easyconfig PR for EasyBuild v5.0.0
        args[1] = '22405'

        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        expected_stdout = '\n'.join([
            "Checking eligibility of easybuilders/easybuild-easyconfigs PR #22405 for merging...",
            "* targets develop branch: OK",
            "* test suite passes: OK",
            "* last test report is successful: OK",
            "* no pending change requests: OK",
            "* approved review: OK (by verdurin)",
            "* milestone is set: OK (5.0.0)",
            "* mergeable state is clean: PR is already merged",
            '',
            "Review OK, merging pull request!",
            '',
            "[DRY RUN] Adding comment to easybuild-easyconfigs issue #22405: 'Going in, thanks @PetrKralCZ!'",
            "[DRY RUN] Merged easybuilders/easybuild-easyconfigs pull request #22405",
        ])
        expected_stderr = ''
        self.assertEqual(stderr.strip(), expected_stderr)
        self.assertTrue(stdout.strip().endswith(expected_stdout), "'%s' ends with '%s'" % (stdout, expected_stdout))

        # --merge-pr also works on easyblocks (& framework) PRs
        args = [
            '--merge-pr',
            '3582',
            '--pr-target-repo=easybuild-easyblocks',
            '-D',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
        ]
        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)
        self.assertEqual(stderr.strip(), '')
        expected_stdout = '\n'.join([
            "Checking eligibility of easybuilders/easybuild-easyblocks PR #3582 for merging...",
            "* targets develop branch: OK",
            "* test suite passes: OK",
            "* no pending change requests: OK",
            "* approved review: OK (by hajgato)",
            "* milestone is set: OK (5.0.0)",
            "* mergeable state is clean: PR is already merged",
            '',
            "Review OK, merging pull request!",
        ])
        self.assertIn(expected_stdout, stdout)

    def test_github_empty_pr(self):
        """Test use of --new-pr (dry run only) with no changes"""
        if self.github_token is None:
            print("Skipping test_empty_pr, no GitHub token available?")
            return

        # get file from develop branch
        full_url = URL_SEPARATOR.join([GITHUB_RAW, GITHUB_EB_MAIN, GITHUB_EASYCONFIGS_REPO,
                                       'develop/easybuild/easyconfigs/z/zlib/zlib-1.3.1-GCCcore-14.2.0.eb'])
        ec_fn = os.path.basename(full_url)
        with self.mocked_stdout_stderr():
            ec = download_file(ec_fn, full_url, path=os.path.join(self.test_prefix, ec_fn))
        if not ec:
            self.fail(f"Failed to download {full_url}")

        # try to open new pr with unchanged file
        args = [
            '--new-pr',
            ec,
            '-D',
            '--github-user=%s' % GITHUB_TEST_ACCOUNT,
            '--pr-commit-msg=blabla',
        ]

        error_msg = "No changed files found when comparing to current develop branch."
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, do_build=True, raise_error=True)

    def test_show_config(self):
        """"Test --show-config and --show-full-config."""

        # only retain $EASYBUILD_* environment variables we expect for this test
        retained_eb_env_vars = [
            'EASYBUILD_DEPRECATED',
            'EASYBUILD_IGNORECONFIGFILES',
            'EASYBUILD_INSTALLPATH',
            'EASYBUILD_ROBOT_PATHS',
            'EASYBUILD_SOURCEPATH',
            'EASYBUILD_SOURCEPATH_DATA',
        ]
        for key in os.environ.keys():
            if key.startswith('EASYBUILD_') and key not in retained_eb_env_vars:
                del os.environ[key]

        cfgfile = os.path.join(self.test_prefix, 'test.cfg')
        cfgtxt = '\n'.join([
            "[config]",
            "subdir-modules = mods",
        ])
        write_file(cfgfile, cfgtxt)

        args = ['--configfiles=%s' % cfgfile, '--show-config', '--buildpath=/weird/build/dir']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        default_prefix = os.path.join(os.environ['HOME'], '.local', 'easybuild')

        test_dir = os.path.dirname(os.path.abspath(__file__))
        expected_lines = [
            r"#",
            r"# Current EasyBuild configuration",
            r"# \(C: command line argument, D: default value, E: environment variable, F: configuration file\)",
            r"#",
            r"buildpath\s* \(C\) = /weird/build/dir",
            r"configfiles\s* \(C\) = .*" + cfgfile,
            r"containerpath\s* \(D\) = %s" % os.path.join(default_prefix, 'containers'),
            r"deprecated\s* \(E\) = 10000000",
            r"ignoreconfigfiles\s* \(E\) = %s" % ', '.join(os.environ['EASYBUILD_IGNORECONFIGFILES'].split(',')),
            r"installpath\s* \(E\) = " + os.path.join(self.test_prefix, 'tmp.*'),
            r"repositorypath\s* \(D\) = " + os.path.join(default_prefix, 'ebfiles_repo'),
            r"robot-paths\s* \(E\) = " + os.path.join(test_dir, 'easyconfigs', 'test_ecs'),
            r"rpath\s* \(D\) = " + ('False' if get_os_type() == DARWIN else 'True'),
            r"sourcepath\s* \(E\) = " + os.path.join(test_dir, 'sandbox', 'sources'),
            r"sourcepath-data\s* \(E\) = " + os.path.join(test_dir, 'sandbox', 'data_sources'),
            r"subdir-modules\s* \(F\) = mods",
        ]

        regex = re.compile('\n'.join(expected_lines))
        self.assertTrue(regex.match(txt), "Pattern '%s' found in: %s" % (regex.pattern, txt))

        args = ['--configfiles=%s' % cfgfile, '--show-full-config', '--buildpath=/weird/build/dir']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False)

        # output of --show-full-config includes additional lines for options with default values
        expected_lines.extend([
            r"force\s* \(D\) = False",
            r"modules-tool\s* \(D\) = Lmod",
            r"module-syntax\s* \(D\) = Lua",
            r"umask\s* \(D\) = None",
        ])

        self._assert_regexs(expected_lines, txt)

        # --show-config should also work if no configuration files are available
        # (existing config files are ignored via $EASYBUILD_IGNORECONFIGFILES)
        self.assertFalse(os.environ.get('EASYBUILD_CONFIGFILES', False))
        args = ['--show-config', '--buildpath=/weird/build/dir']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)
        self.assertRegex(txt, r"buildpath\s* \(C\) = /weird/build/dir")

        # --show-config should not break including of easyblocks via $EASYBUILD_INCLUDE_EASYBLOCKS (see bug #1696)
        txt = '\n'.join([
            'from easybuild.framework.easyblock import EasyBlock',
            'class EB_testeasyblocktoinclude(EasyBlock):',
            '   pass',
            ''
        ])
        testeasyblocktoinclude = os.path.join(self.test_prefix, 'testeasyblocktoinclude.py')
        write_file(testeasyblocktoinclude, txt)

        os.environ['EASYBUILD_INCLUDE_EASYBLOCKS'] = testeasyblocktoinclude
        args = ['--show-config']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)
        regex = re.compile(r'^include-easyblocks \(E\) = .*/testeasyblocktoinclude.py$', re.M)
        self.assertRegex(txt, regex)

    def test_show_config_cfg_levels(self):
        """Test --show-config in relation to how configuring across multiple configuration levels interacts with it."""

        # make sure default module syntax is used
        os.environ.pop('EASYBUILD_MODULE_SYNTAX', None)

        # configuring --modules-tool and --module-syntax on different levels should NOT cause problems
        # cfr. bug report https://github.com/easybuilders/easybuild-framework/issues/2564
        os.environ['EASYBUILD_MODULES_TOOL'] = 'EnvironmentModules'
        args = [
            '--module-syntax=Tcl',
            '--show-config',
        ]
        # set init_config to False to avoid that eb_main (called by _run_mock_eb) re-initialises configuration
        # this fails because $EASYBUILD_MODULES_TOOL=EnvironmentModules conflicts with default module syntax (Lua)
        stdout, _ = self._run_mock_eb(args, raise_error=True, redo_init_config=False)

        patterns = [
            r"^# Current EasyBuild configuration",
            r"^module-syntax\s*\(C\) = Tcl",
            r"^modules-tool\s*\(E\) = EnvironmentModules",
        ]
        self._assert_regexs(patterns, stdout)

    def test_modules_tool_vs_syntax_check(self):
        """Verify that check for modules tool vs syntax works."""

        # make sure default module syntax is used
        os.environ.pop('EASYBUILD_MODULE_SYNTAX', None)

        # using EnvironmentModules modules tool with default module syntax (Lua) is a problem
        os.environ['EASYBUILD_MODULES_TOOL'] = 'EnvironmentModules'
        args = ['--show-full-config']
        error_pattern = "Generating Lua module files requires Lmod as modules tool"
        self.assertErrorRegex(EasyBuildError, error_pattern, self._run_mock_eb, args, raise_error=True)

        patterns = [
            r"^# Current EasyBuild configuration",
            r"^module-syntax\s*\(C\) = Tcl",
            r"^modules-tool\s*\(E\) = EnvironmentModules",
        ]

        # EnvironmentModules modules tool + Tcl module syntax is fine
        args.append('--module-syntax=Tcl')
        stdout, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, redo_init_config=False)
        self._assert_regexs(patterns, stdout)

        # default modules tool (Lmod) with Tcl module syntax is also fine
        del os.environ['EASYBUILD_MODULES_TOOL']
        patterns[-1] = r"^modules-tool\s*\(D\) = Lmod"
        stdout, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, redo_init_config=False)
        self._assert_regexs(patterns, stdout)

    def test_prefix_option(self):
        """Test which configuration settings are affected by --prefix."""
        txt, _ = self._run_mock_eb(['--show-full-config', '--prefix=%s' % self.test_prefix], raise_error=True)

        regex = re.compile(r"(?P<cfg_opt>\S*).*%s.*" % self.test_prefix, re.M)

        expected = [
            'buildpath',
            'containerpath',
            'installpath',
            'packagepath',
            'prefix',
            'repositorypath',
        ]
        self.assertEqual(sorted(regex.findall(txt)), expected)

    def test_dump_env_script(self):
        """Test for --dump-env-script."""

        fftw = 'FFTW-3.3.7-gompic-2018a'
        gcc = 'GCC-4.9.2'
        openmpi = 'OpenMPI-2.1.2-GCC-4.6.4'
        args = ['%s.eb' % ec for ec in [fftw, gcc, openmpi]] + ['--dump-env-script']

        os.chdir(self.test_prefix)
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        for name in [fftw, gcc, openmpi]:
            # check stdout
            regex = re.compile("^Script to set up build environment for %s.eb dumped to %s.env" % (name, name), re.M)
            self.assertRegex(txt, regex)

            # check whether scripts were dumped
            env_script = os.path.join(self.test_prefix, '%s.env' % name)
            self.assertExists(env_script)

        # existing .env files are not overwritten, unless forced
        os.chdir(self.test_prefix)
        args = ['%s.eb' % openmpi, '--dump-env-script']
        error_msg = r"Script\(s\) already exists, not overwriting them \(unless --force is used\): %s.env" % openmpi
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, do_build=True, raise_error=True)

        os.chdir(self.test_prefix)
        args.append('--force')
        self._run_mock_eb(args, do_build=True, raise_error=True)

        # check contents of script
        env_script = os.path.join(self.test_prefix, '%s.env' % openmpi)
        txt = read_file(env_script)
        patterns = [
            "module load GCC/4.6.4",  # loading of toolchain module
            "module load hwloc/1.11.8-GCC-4.6.4",  # loading of dependency module
            # defining build env
            "export FC='gfortran'",
            "export CFLAGS='-O2 -ftree-vectorize -m(arch|cpu)=native -fno-math-errno'",
        ]
        self._assert_regexs(patterns, txt)

        with self.mocked_stdout_stderr():
            res = run_shell_cmd(f"function module {{ echo $@; }} && source {env_script} && echo FC: $FC")
        expected_out = '\n'.join([
            "load GCC/4.6.4",
            "load hwloc/1.11.8-GCC-4.6.4",
            "FC: gfortran",
        ])
        self.assertEqual(res.output.strip(), expected_out)

    def test_dump_env_script_existing_module(self):
        toy_ec = 'toy-0.0.eb'

        os.chdir(self.test_prefix)
        self._run_mock_eb([toy_ec, '--force'], do_build=True)
        env_script = os.path.join(self.test_prefix, os.path.splitext(toy_ec)[0] + '.env')
        test_module = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            test_module += '.lua'
        self.assertExists(test_module)
        self.assertNotExists(env_script)

        args = [toy_ec, '--dump-env']
        os.chdir(self.test_prefix)
        self._run_mock_eb(args, do_build=True, raise_error=True)
        self.assertExists(env_script)
        self.assertExists(test_module)
        module_content = read_file(test_module)
        env_file_content = read_file(env_script)

        error_msg = (r"Script\(s\) already exists, not overwriting them \(unless --force is used\): "
                     + os.path.basename(env_script))
        os.chdir(self.test_prefix)
        self.assertErrorRegex(EasyBuildError, error_msg, self._run_mock_eb, args, do_build=True, raise_error=True)
        self.assertExists(env_script)
        self.assertExists(test_module)
        # Unchanged module and env file
        self.assertEqual(read_file(test_module), module_content)
        self.assertEqual(read_file(env_script), env_file_content)

        args.append('--force')
        os.chdir(self.test_prefix)
        self._run_mock_eb(args, do_build=True, raise_error=True)
        self.assertExists(env_script)
        self.assertExists(test_module)
        # Unchanged module and env file
        self.assertEqual(read_file(test_module), module_content)
        self.assertEqual(read_file(env_script), env_file_content)

    def test_stop(self):
        """Test use of --stop."""
        args = ['toy-0.0.eb', '--force', '--stop=configure']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        regex = re.compile(r"COMPLETED: Installation STOPPED successfully \(took .* secs?\)", re.M)
        self.assertRegex(txt, regex)

        # 'source' step was renamed to 'extract' in EasyBuild 5.0,
        # see https://github.com/easybuilders/easybuild-framework/pull/4629
        args = ['toy-0.0.eb', '--force', '--stop=source']
        _, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)
        self.assertIn("option --stop: invalid choice: 'source' (choose from", stderr)

    def test_fetch(self):
        """Test use of --fetch"""
        options = EasyBuildOptions(go_args=['--fetch'])

        self.assertTrue(options.options.fetch)
        self.assertEqual(options.options.stop, 'fetch')
        self.assertEqual(options.options.modules_tool, None)
        self.assertTrue(options.options.ignore_locks)
        self.assertTrue(options.options.ignore_osdeps)

        # in this test we want to fake the case were no modules tool are in the system so tweak it
        self.modtool = None

        # create lock dir to see whether --fetch trips over it (it shouldn't)
        lock_fn = os.path.join(self.test_installpath, 'software', 'toy', '0.0').replace('/', '_') + '.lock'
        lock_path = os.path.join(self.test_installpath, 'software', '.locks', lock_fn)
        mkdir(lock_path, parents=True)

        # Run for a "regular" EC and one with an external module dependency
        # which might trip up the dependency resolution (see #4298)
        for ec in ('toy-0.0.eb', 'toy-0.0-deps.eb'):
            args = [ec, '--fetch']
            stdout, _ = self._run_mock_eb(args, raise_error=True, strip=True, testing=False)

            patterns = [
                r"^== fetching files and verifying checksums\.\.\.$",
                r"^== COMPLETED: Installation STOPPED successfully \(took .* secs?\)$",
            ]
            self._assert_regexs(patterns, stdout)
            self.assertNotRegex(stdout, r"^== creating build dir, resetting environment\.\.\.$")

        # --fetch should also verify the checksums
        tmpdir = tempfile.mkdtemp(prefix='easybuild-sources')
        write_file(os.path.join(tmpdir, 'toy-0.0.tar.gz'), 'Make checksum check fail')
        args = ['--sourcepath=%s:%s' % (tmpdir, self.test_sourcepath), '--fetch', 'toy-0.0.eb']
        with self.mocked_stdout_stderr():
            pattern = 'Checksum verification for .*/toy-0.0.tar.gz .*failed'
            self.assertErrorRegex(EasyBuildError, pattern, self.eb_main, args, do_build=True, raise_error=True)
            # We can avoid that failure by ignoring the checksums
            args.append('--ignore-checksums')
            self.eb_main(args, do_build=True, raise_error=True)

    def test_parse_external_modules_metadata(self):
        """Test parse_external_modules_metadata function."""
        # by default, provided external module metadata cfg files are picked up
        metadata = parse_external_modules_metadata(None)

        # just a selection
        for mod in ['cray-libsci/13.2.0', 'cray-netcdf/4.3.2', 'fftw/3.3.4.3']:
            self.assertIn(mod, metadata)

        netcdf = {
            'name': ['netCDF', 'netCDF-Fortran'],
            'version': ['4.3.2', '4.3.2'],
            'prefix': 'NETCDF_DIR',
        }
        self.assertEqual(metadata['cray-netcdf/4.3.2'], netcdf)

        libsci = {
            'name': ['LibSci'],
            'version': ['13.2.0'],
            'prefix': 'CRAY_LIBSCI_PREFIX_DIR',
        }
        self.assertEqual(metadata['cray-libsci/13.2.0'], libsci)

        testcfgtxt = EXTERNAL_MODULES_METADATA
        testcfg = os.path.join(self.test_prefix, 'test_external_modules_metadata.cfg')
        write_file(testcfg, testcfgtxt)

        metadata = parse_external_modules_metadata([testcfg])

        # default metadata is overruled, and not available anymore
        for mod in ['cray-libsci/13.2.0', 'cray-netcdf/4.3.2', 'fftw/3.3.4.3']:
            self.assertNotIn(mod, metadata)

        foobar1 = {
            'name': ['foo', 'bar'],
            'version': ['1.2.3', '3.2.1'],
            'prefix': 'FOOBAR_DIR',
        }
        self.assertEqual(metadata['foobar/1.2.3'], foobar1)

        foobar2 = {
            'name': ['foobar'],
            'version': ['2.0'],
            'prefix': 'FOOBAR_PREFIX',
        }
        self.assertEqual(metadata['foobar/2.0'], foobar2)

        # impartial metadata is fine
        self.assertEqual(metadata['foo'], {'name': ['Foo'], 'prefix': '/foo'})
        self.assertEqual(metadata['bar/1.2.3'], {'name': ['bar'], 'version': ['1.2.3']})

        # if both names and versions are specified, lists must have same lengths
        write_file(testcfg, '\n'.join(['[foo/1.2.3]', 'name = foo,bar', 'version = 1.2.3']))
        err_msg = "Different length for lists of names/versions in metadata for external module"
        self.assertErrorRegex(EasyBuildError, err_msg, parse_external_modules_metadata, [testcfg])

        # if path to non-existing file is used, an error is reported
        doesnotexist = os.path.join(self.test_prefix, 'doesnotexist')
        error_pattern = "Specified path for file with external modules metadata does not exist"
        self.assertErrorRegex(EasyBuildError, error_pattern, parse_external_modules_metadata, [doesnotexist])

        # glob pattern can be used to specify file locations to parse_external_modules_metadata
        cfg1 = os.path.join(self.test_prefix, 'cfg_one.ini')
        write_file(cfg1, '\n'.join(['[one/1.0]', 'name = one', 'version = 1.0']))
        cfg2 = os.path.join(self.test_prefix, 'cfg_two.ini')
        write_file(cfg2, '\n'.join([
            '[two/2.0]', 'name = two', 'version = 2.0',
            '[two/2.1]', 'name = two', 'version = 2.1',
        ]))
        cfg3 = os.path.join(self.test_prefix, 'cfg3.ini')
        write_file(cfg3, '\n'.join(['[three/3.0]', 'name = three', 'version = 3.0']))
        cfg4 = os.path.join(self.test_prefix, 'cfg_more.ini')
        write_file(cfg4, '\n'.join(['[one/1.2.3]', 'name = one', 'version = 1.2.3', 'prefix = /one/1.2.3/']))

        metadata = parse_external_modules_metadata([os.path.join(self.test_prefix, 'cfg*.ini')])

        self.assertEqual(sorted(metadata.keys()), ['one/1.0', 'one/1.2.3', 'three/3.0', 'two/2.0', 'two/2.1'])
        self.assertEqual(metadata['one/1.0'], {'name': ['one'], 'version': ['1.0']})
        self.assertEqual(metadata['one/1.2.3'], {'name': ['one'], 'version': ['1.2.3'], 'prefix': '/one/1.2.3/'})
        self.assertEqual(metadata['two/2.0'], {'name': ['two'], 'version': ['2.0']})
        self.assertEqual(metadata['two/2.1'], {'name': ['two'], 'version': ['2.1']})
        self.assertEqual(metadata['three/3.0'], {'name': ['three'], 'version': ['3.0']})

        # check whether entries with unknown keys result in an error
        cfg1 = os.path.join(self.test_prefix, 'broken_cfg1.cfg')
        write_file(cfg1, "[one/1.0]\nname = one\nversion = 1.0\nfoo = bar")
        cfg2 = os.path.join(self.test_prefix, 'cfg2.cfg')
        write_file(cfg2, "[two/2.0]\nname = two\nversion = 2.0")
        cfg3 = os.path.join(self.test_prefix, 'broken_cfg3.cfg')
        write_file(cfg3, "[three/3.0]\nnaem = three\nzzz=zzz\nvresion = 3.0\naaa = aaa")
        cfg4 = os.path.join(self.test_prefix, 'broken_cfg4.cfg')
        write_file(cfg4, "[four/4]\nprfeix = /software/four/4")
        broken_cfgs = [cfg1, cfg2, cfg3, cfg4]
        error_pattern = '\n'.join([
            r"Found metadata entries with unknown keys:",
            r"\* four/4: prfeix",
            r"\* one/1.0: foo",
            r"\* three/3.0: aaa, naem, vresion, zzz",
        ])
        self.assertErrorRegex(EasyBuildError, error_pattern, parse_external_modules_metadata, broken_cfgs)

    def test_zip_logs(self):
        """Test use of --zip-logs"""

        toy_eb_install_dir = os.path.join(self.test_installpath, 'software', 'toy', '0.0', 'easybuild')
        for zip_logs in ['', '--zip-logs', '--zip-logs=gzip', '--zip-logs=bzip2']:

            shutil.rmtree(self.test_installpath)

            args = ['toy-0.0.eb', '--force', '--debug']
            if zip_logs:
                args.append(zip_logs)
            with self.mocked_stdout_stderr():
                self.eb_main(args, do_build=True)

            logs = glob.glob(os.path.join(toy_eb_install_dir, 'easybuild-toy-0.0*log*'))
            self.assertEqual(len(logs), 1, "Found exactly 1 log file in %s: %s" % (toy_eb_install_dir, logs))

            zip_logs_arg = zip_logs.split('=')[-1]
            if zip_logs == '--zip-logs' or zip_logs_arg == 'gzip':
                ext = 'log.gz'
            elif zip_logs_arg == 'bzip2':
                ext = 'log.bz2'
            else:
                ext = 'log'

            self.assertTrue(logs[0].endswith(ext), "%s has correct '%s' extension for %s" % (logs[0], ext, zip_logs))

    def test_debug_lmod(self):
        """Test use of --debug-lmod."""
        if isinstance(self.modtool, Lmod):
            init_config(build_options={'debug_lmod': True})
            out = self.modtool.run_module('avail', return_output=True)

            self._assert_regexs([r"^Lmod version", r"^lmod\(--terse -D avail\)\{", ":avail"], out)
        else:
            print("Skipping test_debug_lmod, requires Lmod as modules tool")

    def test_use_color(self):
        """Test use_color function."""
        self.assertTrue(use_color('always'))
        self.assertFalse(use_color('never'))
        easybuild.tools.options.terminal_supports_colors = lambda _: True
        self.assertTrue(use_color('auto'))
        easybuild.tools.options.terminal_supports_colors = lambda _: False
        self.assertFalse(use_color('auto'))

    def test_list_prs(self):
        """Test --list-prs."""
        args = ['--list-prs', 'foo']
        error_msg = r"must be one of \['open', 'closed', 'all'\]"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        args = ['--list-prs', 'open,foo']
        error_msg = r"must be one of \['created', 'updated', 'popularity', 'long-running'\]"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        args = ['--list-prs', 'open,created,foo']
        error_msg = r"must be one of \['asc', 'desc'\]"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        args = ['--list-prs', 'open,created,asc,foo']
        error_msg = r"must be in the format 'state\[,order\[,direction\]\]"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, args, raise_error=True)

        args = ['--list-prs', 'closed,updated,asc']
        txt, _ = self._run_mock_eb(args, testing=False)
        expected = "Listing PRs with parameters: direction=asc, per_page=100, sort=updated, state=closed"
        self.assertIn(expected, txt)

    def test_list_software(self):
        """Test --list-software and --list-installed-software."""

        # copy selected test easyconfigs for testing --list-*software options with;
        # full test is a nuisance, because all dependencies must be available and toolchains like intel must have
        # all expected components when testing with HierarchicalMNS (which the test easyconfigs don't always have)
        topdir = os.path.dirname(os.path.abspath(__file__))

        cray_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'c', 'CrayCCE', 'CrayCCE-5.1.29.eb')
        gcc_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'g', 'GCC', 'GCC-4.6.3.eb')
        gzip_ec = os.path.join(topdir, 'easyconfigs', 'v1.0', 'g', 'gzip', 'gzip-1.4-GCC-4.6.3.eb')
        gzip_system_ec = os.path.join(topdir, 'easyconfigs', 'v1.0', 'g', 'gzip', 'gzip-1.4.eb')

        test_ecs = os.path.join(self.test_prefix, 'test_ecs')
        for ec in [cray_ec, gcc_ec, gzip_ec, gzip_system_ec]:
            subdirs = os.path.dirname(ec).split(os.path.sep)[-2:]
            target_dir = os.path.join(test_ecs, *subdirs)
            mkdir(target_dir, parents=True)
            copy_file(ec, target_dir)

        # add (fake) HPL easyconfig using CrayCCE toolchain
        # (required to trigger bug reported in https://github.com/easybuilders/easybuild-framework/issues/3265)
        hpl_cray_ec_txt = '\n'.join([
            'easyblock = "ConfigureMake"',
            'name = "HPL"',
            'version = "2.3"',
            "homepage = 'http://www.netlib.org/benchmark/hpl/'",
            'description = "HPL"',
            'toolchain = {"name": "CrayCCE", "version": "5.1.29"}',
        ])
        hpl_cray_ec = os.path.join(self.test_prefix, 'test_ecs', 'h', 'HPL', 'HPL-2.3-CrayCCE-5.1.29.eb')
        write_file(hpl_cray_ec, hpl_cray_ec_txt)

        # put dummy Core/GCC/4.6.3 in place
        modpath = os.path.join(self.test_prefix, 'modules')
        write_file(os.path.join(modpath, 'Core', 'GCC', '4.6.3'), '#%Module')
        self.modtool.use(modpath)

        # test with different module naming scheme active
        # (see https://github.com/easybuilders/easybuild-framework/issues/3265)
        for mns in ['EasyBuildMNS', 'HierarchicalMNS']:

            args = [
                '--list-software',
                '--robot-paths=%s' % test_ecs,
                '--module-naming-scheme=%s' % mns,
            ]
            txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, verbose=True)

            patterns = [
                r"^.*\s*== Processed 5/5 easyconfigs...",
                r"^== Found 4 different software packages",
                r"^\* CrayCCE",
                r"^\* GCC",
                r"^\* gzip",
                r"^\* HPL",
            ]
            self._assert_regexs(patterns, txt)

            args = [
                '--list-software=detailed',
                '--output-format=rst',
                '--robot-paths=%s' % test_ecs,
                '--module-naming-scheme=%s' % mns,
            ]
            txt, _ = self._run_mock_eb(args, testing=False, raise_error=True, verbose=True)

            patterns = [
                r"^.*\s*== Processed 5/5 easyconfigs...",
                r"^== Found 4 different software packages",
                r'^\*CrayCCE\*',
                r'^``5.1.29``\s+``system``',
                r'^\*GCC\*',
                r'^``4.6.3``\s+``system``',
                r'^\*gzip\*',
                r'^``1.4``    ``GCC/4.6.3``, ``system``',
            ]
            self._assert_regexs(patterns, txt)

            args = [
                '--list-installed-software',
                '--output-format=rst',
                '--robot-paths=%s' % test_ecs,
                '--module-naming-scheme=%s' % mns,
            ]
            txt, _ = self._run_mock_eb(args, testing=False, raise_error=True, verbose=True)

            patterns = [
                r"^.*\s*== Processed 5/5 easyconfigs...",
                r"^== Found 4 different software packages",
                r"^== Retained 1 installed software packages",
                r'^\* GCC',
            ]
            self._assert_regexs(patterns, txt)

            self.assertNotIn('gzip', txt)
            self.assertNotIn('CrayCCE', txt)

            args = [
                '--list-installed-software=detailed',
                '--robot-paths=%s' % test_ecs,
                '--module-naming-scheme=%s' % mns,
            ]
            txt, _ = self._run_mock_eb(args, testing=False, raise_error=True, verbose=True)

            patterns = [
                r"^.*\s*== Processed 5/5 easyconfigs...",
                r"^== Found 4 different software packages",
                r"^== Retained 1 installed software packages",
                r'^\* GCC',
                r'^\s+\* GCC v4.6.3: system',
            ]
            self._assert_regexs(patterns, txt)

            self.assertNotIn('gzip', txt)
            self.assertNotIn('CrayCCE', txt)

    def test_parse_optarch(self):
        """Test correct parsing of optarch option."""

        # Check that it is not parsed if we are submitting a job
        options = EasyBuildOptions(go_args=['--job'])
        optarch_string = 'Intel:something;GCC:somethinglese'
        options.options.optarch = optarch_string
        options.postprocess()
        self.assertEqual(options.options.optarch, optarch_string)

        # Use no arguments for the rest of the tests
        options = EasyBuildOptions()

        # Check for EasyBuildErrors
        error_msg = "The optarch option has an incorrect syntax"
        options.options.optarch = 'Intel:something;GCC'
        self.assertErrorRegex(EasyBuildError, error_msg, options.postprocess)

        options.options.optarch = 'Intel:something;'
        self.assertErrorRegex(EasyBuildError, error_msg, options.postprocess)

        options.options.optarch = 'Intel:something:somethingelse'
        self.assertErrorRegex(EasyBuildError, error_msg, options.postprocess)

        error_msg = "The optarch option contains duplicated entries for compiler"
        options.options.optarch = 'Intel:something;GCC:somethingelse;Intel:anothersomething'
        self.assertErrorRegex(EasyBuildError, error_msg, options.postprocess)

        # Check the parsing itself
        gcc_generic_flags = "march=x86-64 -mtune=generic"
        test_cases = [
            ('', ''),
            ('xHost', 'xHost'),
            ('GENERIC', 'GENERIC'),
            ('Intel:xHost', {'Intel': 'xHost'}),
            ('Intel:GENERIC', {'Intel': 'GENERIC'}),
            ('Intel:xHost;GCC:%s' % gcc_generic_flags, {'Intel': 'xHost', 'GCC': gcc_generic_flags}),
            ('Intel:;GCC:%s' % gcc_generic_flags, {'Intel': '', 'GCC': gcc_generic_flags}),
        ]

        for optarch_string, optarch_parsed in test_cases:
            options.options.optarch = optarch_string
            options.postprocess()
            self.assertEqual(options.options.optarch, optarch_parsed)

    def test_check_contrib_style(self):
        """Test style checks performed by --check-contrib + dedicated --check-style option."""
        if 'pycodestyle' not in sys.modules:
            print("Skipping test_check_contrib_style pycodestyle is not available")
            return

        regex = re.compile(r"Running style check on 2 easyconfig\(s\)(.|\n)*>> All style checks PASSed!", re.M)
        args = [
            '--check-style',
            'GCC-4.9.2.eb',
            'toy-0.0.eb',
        ]
        stdout, _ = self._run_mock_eb(args, raise_error=True)
        self.assertRegex(stdout, regex)

        # --check-contrib fails because of missing checksums, but style test passes
        args[0] = '--check-contrib'
        error_pattern = "One or more contribution checks FAILED"
        with self.mocked_stdout_stderr(mock_stderr=False):
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)
            stdout = self.get_stdout().strip()
        self.assertRegex(stdout, regex)

        # copy toy-0.0.eb test easyconfig, fiddle with it to make style check fail
        toy = os.path.join(self.test_prefix, 'toy.eb')
        copy_file(os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb'), toy)

        toytxt = read_file(toy)
        # introduce whitespace issues
        toytxt = toytxt.replace("name = 'toy'", "name\t='toy'    ")
        # introduce long line
        toytxt = toytxt.replace('description = "Toy C program, 100% toy."', 'description = "%s"' % ('toy ' * 30))
        write_file(toy, toytxt)

        for check_type in ['contribution', 'style']:
            args = [
                '--check-%s' % check_type[:7],
                toy,
            ]
            error_pattern = "One or more %s checks FAILED!" % check_type
            with self.mocked_stdout_stderr(mock_stderr=False):
                self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)
                stdout = self.get_stdout()
            patterns = [
                "toy.eb:1:5: E223 tab before operator",
                "toy.eb:1:7: E225 missing whitespace around operator",
                "toy.eb:1:12: W299 trailing whitespace",
                r"toy.eb:5:121: E501 line too long \(136 > 120 characters\)",
            ]
            self._assert_regexs(patterns, stdout)

    def test_check_contrib_non_style(self):
        """Test non-style checks performed by --check-contrib."""

        if 'pycodestyle' not in sys.modules:
            print("Skipping test_check_contrib_non_style pycodestyle is not available")
            return

        args = [
            '--check-contrib',
            'toy-0.0.eb',
        ]
        error_pattern = "One or more contribution checks FAILED"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, raise_error=True)
            stdout = self.get_stdout().strip()
            stderr = self.get_stderr().strip()
        self.assertEqual(stderr, '')

        # SHA256 checksum checks fail
        patterns = [
            r"\[FAIL\] .*/toy-0.0.eb$",
            r"^Checksums missing for one or more sources/patches in toy-0.0.eb: "
            r"found 1 sources \+ 2 patches vs 1 checksums$",
            r"^>> One or more SHA256 checksums checks FAILED!",
        ]
        self._assert_regexs(patterns, stdout)

        # --check-contrib passes if None values are used as checksum, but produces warning
        toy = os.path.join(self.test_prefix, 'toy.eb')
        copy_file(os.path.join(os.path.dirname(__file__), 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb'), toy)
        toytxt = read_file(toy)
        toytxt = toytxt + '\n'.join([
            'checksums = [',
            "    None,  # toy-0.0.tar.gz",
            "    # toy-0.0_fix-silly-typo-in-printf-statement.patch",
            "    '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487',",
            "    '4196b56771140d8e2468fb77f0240bc48ddbf5dabafe0713d612df7fafb1e458',  # toy-extra.txt",
            ']\n',
        ])
        write_file(toy, toytxt)

        args = ['--check-contrib', toy]
        with self.mocked_stdout_stderr():
            self.eb_main(args, raise_error=True)
            stderr = self.get_stderr().strip()
        self.assertEqual(stderr, "WARNING: Found 1 None checksum value(s), please make sure this is intended!")

    def test_allow_use_as_root(self):
        """Test --allow-use-as-root-and-accept-consequences"""

        # pretend we're running as root by monkey patching os.getuid used in main
        easybuild.main.os.getuid = lambda: 0

        # running as root is disallowed by default
        error_msg = "You seem to be running EasyBuild with root privileges which is not wise, so let's end this here"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_msg, self.eb_main, ['toy-0.0.eb'], raise_error=True)

        # running as root is allowed under --allow-use-as-root, but does result in a warning being printed to stderr
        args = ['toy-0.0.eb', '--allow-use-as-root-and-accept-consequences']
        _, stderr = self._run_mock_eb(args, raise_error=True, strip=True)

        expected = "WARNING: Using EasyBuild as root is NOT recommended, please proceed with care!\n"
        expected += "(this is only allowed because EasyBuild was configured with "
        expected += "--allow-use-as-root-and-accept-consequences)"
        self.assertEqual(stderr, expected)

    def test_verify_easyconfig_filenames(self):
        """Test --verify-easyconfig-filename"""
        test_easyconfigs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs')
        fd, dummylogfn = tempfile.mkstemp(prefix='easybuild-dummy', suffix='.log')
        os.close(fd)

        toy_ec = os.path.join(test_easyconfigs_dir, 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        copy_file(toy_ec, test_ec)

        args = [
            test_ec,
            '--dry-run',  # implies enabling dependency resolution
            '--unittest-file=%s' % self.logfile,
        ]

        # filename of provided easyconfig doesn't matter by default
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)
        self.assertIn('module: toy/0.0', logtxt)

        write_file(self.logfile, '')

        # when --verify-easyconfig-filenames is enabled, EB gets picky about the easyconfig filename
        args.append('--verify-easyconfig-filenames')
        error_pattern = r"Easyconfig filename 'test.eb' does not match with expected filename 'toy-0.0.eb' \(specs: "
        error_pattern += r"name: 'toy'; version: '0.0'; versionsuffix: ''; "
        error_pattern += r"toolchain name, version: 'system', 'system'\)"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, logfile=dummylogfn,
                                  raise_error=True)

        write_file(self.logfile, '')

        args[0] = toy_ec
        with self.mocked_stdout_stderr():
            self.eb_main(args, logfile=dummylogfn, raise_error=True)
        logtxt = read_file(self.logfile)
        self.assertIn('module: toy/0.0', logtxt)

    def test_set_default_module(self):
        """Test use of --set-default-module"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-deps.eb')

        with self.mocked_stdout_stderr():
            self.eb_main([toy_ec, '--set-default-module'], do_build=True, raise_error=True)

        toy_mod_dir = os.path.join(self.test_installpath, 'modules', 'all', 'toy')
        toy_mod = os.path.join(toy_mod_dir, '0.0-deps')
        if get_module_syntax() == 'Lua':
            toy_mod += '.lua'

        self.assertExists(toy_mod)

        if get_module_syntax() == 'Lua':
            self.assertTrue(os.path.islink(os.path.join(toy_mod_dir, 'default')))
            self.assertEqual(os.readlink(os.path.join(toy_mod_dir, 'default')), '0.0-deps.lua')
        elif get_module_syntax() == 'Tcl':
            toy_dot_version = os.path.join(toy_mod_dir, '.version')
            self.assertExists(toy_dot_version)
            toy_dot_version_txt = read_file(toy_dot_version)
            self.assertIn("set ModulesVersion 0.0-deps", toy_dot_version_txt)
        else:
            self.fail("Uknown module syntax: %s" % get_module_syntax())

        # make sure default is also set for moduleclass symlink
        toy_mod_symlink_dir = os.path.join(self.test_installpath, 'modules', 'tools', 'toy')
        if get_module_syntax() == 'Lua':
            self.assertEqual(sorted(os.listdir(toy_mod_symlink_dir)), ['0.0-deps.lua', 'default'])
            default_symlink = os.path.join(toy_mod_symlink_dir, 'default')
            mod_symlink = os.path.join(toy_mod_symlink_dir, '0.0-deps.lua')
            self.assertTrue(os.path.islink(default_symlink))
            self.assertTrue(os.path.islink(mod_symlink))
            self.assertEqual(os.readlink(default_symlink), '0.0-deps.lua')
            modfile_path = os.path.join(toy_mod_dir, '0.0-deps.lua')
            self.assertTrue(os.path.samefile(os.readlink(mod_symlink), modfile_path))
        elif get_module_syntax() == 'Tcl':
            self.assertEqual(sorted(os.listdir(toy_mod_symlink_dir)), ['.version', '0.0-deps'])
            version_symlink = os.path.join(toy_mod_symlink_dir, '.version')
            mod_symlink = os.path.join(toy_mod_symlink_dir, '0.0-deps')
            self.assertTrue(os.path.islink(version_symlink))
            self.assertTrue(os.path.islink(mod_symlink))
            versionfile_path = os.path.join(toy_mod_dir, '.version')
            self.assertEqual(os.readlink(version_symlink), versionfile_path)
            modfile_path = os.path.join(toy_mod_dir, '0.0-deps')
            self.assertTrue(os.path.samefile(os.readlink(mod_symlink), modfile_path))
        else:
            self.fail("Uknown module syntax: %s" % get_module_syntax())

    def test_set_default_module_robot(self):
        """Test use of --set-default-module --robot."""
        # create two test easyconfigs, one depending on the other
        # (using dummy Toolchain easyblock included in the tests)
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        write_file(test_ec, '\n'.join([
            "easyblock = 'Toolchain'",
            "name = 'test'",
            "version = '1.0'",
            "homepage = 'https://example.com'",
            "description = 'this is just a test'",
            "toolchain = SYSTEM",
            "dependencies = [('thisisjustatestdep', '3.14')]",
        ]))
        testdep_ec = os.path.join(self.test_prefix, 'thisisjustatestdep-3.14.eb')
        write_file(testdep_ec, '\n'.join([
            "easyblock = 'Toolchain'",
            "name = 'thisisjustatestdep'",
            "version = '3.14'",
            "homepage = 'https://example.com'",
            "description = 'this is just a test'",
            "toolchain = SYSTEM",
        ]))

        args = [
            test_ec,
            '--force',
            '--set-default-module',
            '--robot',
            self.test_prefix,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)

        # default module is set for specified easyconfig, but *not* for its dependency
        modfiles_dir = os.path.join(self.test_installpath, 'modules', 'all')
        self.assertEqual(sorted(os.listdir(modfiles_dir)), ['test', 'thisisjustatestdep'])
        test_mod_dir = os.path.join(modfiles_dir, 'test')
        testdep_mod_dir = os.path.join(modfiles_dir, 'thisisjustatestdep')

        if get_module_syntax() == 'Lua':
            # only 'default' symlink for test/1.0, not for thisisjustadep/3.14
            self.assertEqual(sorted(os.listdir(test_mod_dir)), ['1.0.lua', 'default'])
            self.assertEqual(sorted(os.listdir(testdep_mod_dir)), ['3.14.lua'])
            default_symlink = os.path.join(test_mod_dir, 'default')
            self.assertTrue(os.path.islink(default_symlink))
            self.assertEqual(os.readlink(default_symlink), '1.0.lua')
        elif get_module_syntax() == 'Tcl':
            self.assertEqual(sorted(os.listdir(test_mod_dir)), ['.version', '1.0'])
            self.assertEqual(sorted(os.listdir(testdep_mod_dir)), ['3.14'])
            dot_version_file = os.path.join(test_mod_dir, '.version')
            self.assertIn("set ModulesVersion 1.0", read_file(dot_version_file))
        else:
            self.fail("Uknown module syntax: %s" % get_module_syntax())

    def test_inject_checksums(self):
        """Test for --inject-checksums"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-gompi-2018a-test.eb')

        # checksums are injected in existing easyconfig, so test with a copy
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        copy_file(toy_ec, test_ec)

        # if existing checksums are found, --force is required
        args = [test_ec, '--inject-checksums']
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, "Found existing checksums", self.eb_main, args, raise_error=True)
            stdout = self.get_stdout().strip()
            stderr = self.get_stderr().strip()

        # make sure software install directory is *not* created (see bug issue #3064)
        self.assertNotExists(os.path.join(self.test_installpath, 'software', 'toy'))

        # SHA256 is default type of checksums used
        self.assertIn("injecting sha256 checksums in", stdout)
        self.assertEqual(stderr, '')

        args.append('--force')
        stdout, stderr = self._run_mock_eb(args, raise_error=True, strip=True)

        toy_source_sha256 = '44332000aa33b99ad1e00cbd1a7da769220d74647060a10e807b916d73ea27bc'
        toy_patch_sha256 = '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487'
        bar_tar_gz_sha256 = 'f3676716b610545a4e8035087f5be0a0248adee0abb3930d3edb76d498ae91e7'
        bar_patch = 'bar-0.0_fix-silly-typo-in-printf-statement.patch'
        bar_patch_sha256 = '84db53592e882b5af077976257f9c7537ed971cb2059003fd4faa05d02cae0ab'
        bar_patch_bis = 'bar-0.0_fix-very-silly-typo-in-printf-statement.patch'
        bar_patch_bis_sha256 = 'd0bf102f9c5878445178c5f49b7cd7546e704c33fe2060c7354b7e473cfeb52b'
        patterns = [
            r"^== injecting sha256 checksums in .*/test\.eb$",
            r"^== fetching sources & patches for test\.eb\.\.\.$",
            r"^== backup of easyconfig file saved to .*/test\.eb\.bak_[0-9]+_[0-9]+$",
            r"^== injecting sha256 checksums for sources & patches in test\.eb\.\.\.$",
            r"^== \* toy-0.0\.tar\.gz: %s$" % toy_source_sha256,
            r"^== \* toy-0\.0_fix-silly-typo-in-printf-statement\.patch: %s$" % toy_patch_sha256,
            r"^== injecting sha256 checksums for extensions in test\.eb\.\.\.$",
            r"^==  \* bar-0\.0\.tar\.gz: %s$" % bar_tar_gz_sha256,
            r"^==  \* %s: %s$" % (bar_patch, bar_patch_sha256),
            r"^==  \* %s: %s$" % (bar_patch_bis, bar_patch_bis_sha256),
            r"^==  \* barbar-1\.2\.tar\.gz: d5bd9908cdefbe2d29c6f8d5b45b2aaed9fd904b5e6397418bb5094fbdb3d838$",
        ]
        self._assert_regexs(patterns, stdout)

        warning_msg = "WARNING: Found existing checksums in test.eb, overwriting them (due to use of --force)..."
        self.assertEqual(stderr, warning_msg)

        ec_txt = read_file(test_ec)

        # some checks on 'raw' easyconfig contents
        # single-line checksum for barbar extension since there's only one
        self.assertIn("'checksums': ['d5bd9908cdefbe2d29c6f8d5b45b2aaed9fd904b5e6397418bb5094fbdb3d838'],", ec_txt)

        # single-line checksum entry for bar source tarball
        regex = re.compile("^[ ]*{'bar-0.0.tar.gz': '%s'},$" % bar_tar_gz_sha256, re.M)
        self.assertRegex(ec_txt, regex)

        # no single-line checksum entry for bar patches, since line would be > 120 chars
        bar_patch_patterns = [
            r"^[ ]*{'%s':\n[ ]*'%s'},$" % (bar_patch, bar_patch_sha256),
            r"^[ ]*{'%s':\n[ ]*'%s'},$" % (bar_patch_bis, bar_patch_bis_sha256),
            r"^[ ]*'%s',$" % bar_patch,
            r"^[ ]*'%s',$" % bar_patch_bis,
        ]
        self._assert_regexs(bar_patch_patterns, ec_txt)

        # name/version of toy should NOT be hardcoded in exts_list, 'name'/'version' parameters should be used
        self.assertIn('    (name, version, {', ec_txt)

        # make sure checksums are only there once...
        # exactly one definition of 'checksums' easyconfig parameter
        self.assertEqual(re.findall('^checksums', ec_txt, re.M), ['checksums'])
        # exactly three checksum specs for extensions, one list of checksums for each extension
        self.assertEqual(re.findall("[ ]*'checksums'", ec_txt, re.M), ["        'checksums'"] * 3)

        # there should be only one hit for 'source_urls', i.e. the one in exts_default_options
        self.assertEqual(len(re.findall('source_urls*.*$', ec_txt, re.M)), 1)

        # no parse errors for updated easyconfig file...
        ec = EasyConfigParser(test_ec).get_config_dict()
        self.assertEqual(ec['sources'], ['%(name)s-%(version)s.tar.gz'])
        self.assertEqual(ec['patches'], ['toy-0.0_fix-silly-typo-in-printf-statement.patch'])
        self.assertEqual(ec['checksums'], [{'toy-0.0.tar.gz': toy_source_sha256},
                                           {'toy-0.0_fix-silly-typo-in-printf-statement.patch': toy_patch_sha256}])
        self.assertEqual(ec['exts_default_options'], {'source_urls': ['http://example.com/%(name)s']})
        self.assertEqual(ec['exts_list'][0], 'ulimit')
        expected_buildopts = " && gcc bar.c -o anotherbar && "
        expected_buildopts += 'echo "TOY_EXAMPLES=$TOY_EXAMPLES" > %(installdir)s/toy_libs_path.txt'
        self.assertEqual(ec['exts_list'][1], ('bar', '0.0', {
            'buildopts': expected_buildopts,
            'checksums': [
                {'bar-0.0.tar.gz': bar_tar_gz_sha256},
                {'bar-0.0_fix-silly-typo-in-printf-statement.patch': bar_patch_sha256},
                {'bar-0.0_fix-very-silly-typo-in-printf-statement.patch': bar_patch_bis_sha256},
            ],
            'exts_filter': ("cat | grep '^bar$'", '%(name)s'),
            'patches': [bar_patch, bar_patch_bis],
            'toy_ext_param': "mv anotherbar bar_bis",
            'unknowneasyconfigparameterthatshouldbeignored': 'foo',
            'keepsymlinks': False,
        }))
        self.assertEqual(ec['exts_list'][2], ('barbar', '1.2', {
            'checksums': ['d5bd9908cdefbe2d29c6f8d5b45b2aaed9fd904b5e6397418bb5094fbdb3d838'],
            'start_dir': 'src',
        }))

        # backup of easyconfig was created
        ec_backups = glob.glob(test_ec + '.bak_*')
        self.assertEqual(len(ec_backups), 1)
        self.assertEqual(read_file(toy_ec), read_file(ec_backups[0]))

        self.assertIn("injecting sha256 checksums in", stdout)
        self.assertEqual(stderr, warning_msg)

        remove_file(ec_backups[0])

        # if any checksums are present already, it doesn't matter if they're wrong (since they will be replaced)
        ectxt = read_file(test_ec)
        for chksum in ec['checksums'] + [c for e in ec['exts_list'][1:] for c in e[2]['checksums']]:
            if isinstance(chksum, dict):
                chksum = list(chksum.values())[0]
            ectxt = ectxt.replace(chksum, chksum[::-1])
        write_file(test_ec, ectxt)

        stdout, stderr = self._run_mock_eb(args, raise_error=True, strip=True)

        ec = EasyConfigParser(test_ec).get_config_dict()
        self.assertEqual(ec['checksums'], [{'toy-0.0.tar.gz': toy_source_sha256},
                                           {'toy-0.0_fix-silly-typo-in-printf-statement.patch': toy_patch_sha256}])

        ec_backups = glob.glob(test_ec + '.bak_*')
        self.assertEqual(len(ec_backups), 1)
        remove_file(ec_backups[0])

        # also test injecting of MD5 checksums into easyconfig that doesn't include checksums already
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        toy_ec_txt = read_file(toy_ec)

        # get rid of existing checksums
        regex = re.compile(r'^checksums(?:.|\n)*?\]\s*$', re.M)
        toy_ec_txt = regex.sub('', toy_ec_txt)
        self.assertNotIn('checksums = ', toy_ec_txt)

        write_file(test_ec, toy_ec_txt)
        args = [test_ec, '--inject-checksums=sha256']

        stdout, stderr = self._run_mock_eb(args, raise_error=True, strip=True)

        patterns = [
            r"^== injecting sha256 checksums in .*/test\.eb$",
            r"^== fetching sources & patches for test\.eb\.\.\.$",
            r"^== backup of easyconfig file saved to .*/test\.eb\.bak_[0-9]+_[0-9]+$",
            r"^== injecting sha256 checksums for sources & patches in test\.eb\.\.\.$",
            r"^== \* toy-0.0\.tar\.gz: 44332000aa33b99ad1e00cbd1a7da769220d74647060a10e807b916d73ea27bc$",
            r"^== \* toy-0\.0_fix-silly-typo-in-printf-statement\.patch: "  # no comma, continues on next line
            r"81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487$",
            r"^== \* toy-extra\.txt: 4196b56771140d8e2468fb77f0240bc48ddbf5dabafe0713d612df7fafb1e458$",
        ]
        self._assert_regexs(patterns, stdout)

        self.assertEqual(stderr, '')

        # backup of easyconfig was created
        ec_backups = glob.glob(test_ec + '.bak_*')
        self.assertEqual(len(ec_backups), 1)
        self.assertEqual(toy_ec_txt, read_file(ec_backups[0]))

        # no parse errors for updated easyconfig file...
        ec = EasyConfigParser(test_ec).get_config_dict()
        checksums = [
            {'toy-0.0.tar.gz': '44332000aa33b99ad1e00cbd1a7da769220d74647060a10e807b916d73ea27bc'},
            {'toy-0.0_fix-silly-typo-in-printf-statement.patch':
             '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487'},
            {'toy-extra.txt': '4196b56771140d8e2468fb77f0240bc48ddbf5dabafe0713d612df7fafb1e458'},
        ]
        self.assertEqual(ec['checksums'], checksums)

        # check whether empty list of checksums is stripped out by --inject-checksums
        toy_ec_txt = read_file(toy_ec)

        regex = re.compile(r'^checksums(?:.|\n)*?\]\s*$', re.M)
        toy_ec_txt = regex.sub('', toy_ec_txt)

        toy_ec_txt += "\nchecksums = []"

        write_file(test_ec, toy_ec_txt)
        args = [test_ec, '--inject-checksums', '--force']
        self._run_mock_eb(args, raise_error=True, strip=True)

        ec_txt = read_file(test_ec)
        regex = re.compile(r"^checksums = \[\]", re.M)
        self.assertNotRegex(ec_txt, regex)

        ec = EasyConfigParser(test_ec).get_config_dict()
        expected_checksums = [
            {'toy-0.0.tar.gz': '44332000aa33b99ad1e00cbd1a7da769220d74647060a10e807b916d73ea27bc'},
            {'toy-0.0_fix-silly-typo-in-printf-statement.patch':
             '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487'},
            {'toy-extra.txt': '4196b56771140d8e2468fb77f0240bc48ddbf5dabafe0713d612df7fafb1e458'}
        ]
        self.assertEqual(ec['checksums'], expected_checksums)

        # Also works for extensions (all 3 patch formats)
        write_file(test_ec, textwrap.dedent("""
            exts_list = [
               ("bar", "0.0", {
                   'sources': ['bar-0.0-local.tar.gz'],
                   'patches': [
                       'bar-0.0_fix-silly-typo-in-printf-statement.patch',  # normal patch
                       ('bar-0.0_fix-very-silly-typo-in-printf-statement.patch', 0),  # patch with patch level
                       ('toy-0.0_fix-silly-typo-in-printf-statement.patch', 'toy_subdir'),
                   ],
               }),
            ]
        """), append=True)
        self._run_mock_eb(args, raise_error=True, strip=True)
        ec = EasyConfigParser(test_ec).get_config_dict()
        ext = ec['exts_list'][0]
        self.assertEqual((ext[0], ext[1]), ("bar", "0.0"))
        ext_opts = ext[2]
        expected_patches = [
            'bar-0.0_fix-silly-typo-in-printf-statement.patch',
            ('bar-0.0_fix-very-silly-typo-in-printf-statement.patch', 0),
            ('toy-0.0_fix-silly-typo-in-printf-statement.patch', 'toy_subdir')
        ]
        self.assertEqual(ext_opts['patches'], expected_patches)
        expected_checksums = [
            {'bar-0.0-local.tar.gz':
             'f3676716b610545a4e8035087f5be0a0248adee0abb3930d3edb76d498ae91e7'},
            {'bar-0.0_fix-silly-typo-in-printf-statement.patch':
             '84db53592e882b5af077976257f9c7537ed971cb2059003fd4faa05d02cae0ab'},
            {'bar-0.0_fix-very-silly-typo-in-printf-statement.patch':
             'd0bf102f9c5878445178c5f49b7cd7546e704c33fe2060c7354b7e473cfeb52b'},
            {'toy-0.0_fix-silly-typo-in-printf-statement.patch':
             '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487'}
        ]
        self.assertEqual(ext_opts['checksums'], expected_checksums)

        # passing easyconfig filename as argument to --inject-checksums results in error being reported,
        # because it's not a valid type of checksum
        args = ['--inject-checksums', test_ec]
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(SystemExit, '.*', self.eb_main, args, raise_error=True, raise_systemexit=True)
            stdout = self.get_stdout().strip()
            stderr = self.get_stderr().strip()

        self.assertEqual(stdout, '')
        self.assertIn("option --inject-checksums: invalid choice", stderr)

    def test_inject_checksums_to_json(self):
        """Test --inject-checksums-to-json."""

        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        copy_file(toy_ec, test_ec)
        test_ec_txt = read_file(test_ec)

        args = [test_ec, '--inject-checksums-to-json']
        self._run_mock_eb(args, raise_error=True, strip=True)

        self.assertEqual(test_ec_txt, read_file(test_ec))

        checksums_json_txt = read_file(os.path.join(self.test_prefix, 'checksums.json'))
        expected_dict = {
            'bar-0.0.tar.gz': 'f3676716b610545a4e8035087f5be0a0248adee0abb3930d3edb76d498ae91e7',
            'bar-0.0_fix-silly-typo-in-printf-statement.patch':
                '84db53592e882b5af077976257f9c7537ed971cb2059003fd4faa05d02cae0ab',
            'bar-0.0_fix-very-silly-typo-in-printf-statement.patch':
                'd0bf102f9c5878445178c5f49b7cd7546e704c33fe2060c7354b7e473cfeb52b',
            'bar.tgz': '33ac60685a3e29538db5094259ea85c15906cbd0f74368733f4111eab6187c8f',
            'barbar-0.0.tar.gz': 'd5bd9908cdefbe2d29c6f8d5b45b2aaed9fd904b5e6397418bb5094fbdb3d838',
            'toy-0.0.tar.gz': '44332000aa33b99ad1e00cbd1a7da769220d74647060a10e807b916d73ea27bc',
            'toy-0.0_fix-silly-typo-in-printf-statement.patch':
                '81a3accc894592152f81814fbf133d39afad52885ab52c25018722c7bda92487',
            'toy-extra.txt': '4196b56771140d8e2468fb77f0240bc48ddbf5dabafe0713d612df7fafb1e458',
        }
        self.assertEqual(json.loads(checksums_json_txt), expected_dict)

    def test_force_download(self):
        """Test --force-download"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        toy_srcdir = os.path.join(topdir, 'sandbox', 'sources', 'toy')

        copy_file(toy_ec, self.test_prefix)
        toy_tar = 'toy-0.0.tar.gz'
        copy_file(os.path.join(toy_srcdir, toy_tar), os.path.join(self.test_prefix, 't', 'toy', toy_tar))

        toy_ec = os.path.join(self.test_prefix, os.path.basename(toy_ec))
        write_file(toy_ec, "\nsource_urls = ['file://%s']" % toy_srcdir, append=True)

        args = [
            toy_ec,
            '--force',
            '--force-download',
            '--sourcepath=%s' % self.test_prefix,
        ]
        stdout, stderr = self._run_mock_eb(args, do_build=True, raise_error=True, verbose=True, strip=True)
        regex = re.compile(r"^WARNING: Found file toy-0.0.tar.gz at .*, but re-downloading it anyway\.\.\.$")
        self.assertTrue(regex.match(stderr), "Pattern '%s' matches: %s" % (regex.pattern, stderr))

        # check that existing source tarball was backed up
        toy_tar_backups = glob.glob(os.path.join(self.test_prefix, 't', 'toy', '*.bak_*'))
        self.assertEqual(len(toy_tar_backups), 1)
        self.assertTrue(os.path.basename(toy_tar_backups[0]).startswith('toy-0.0.tar.gz.bak_'))

    def test_enforce_checksums(self):
        """Test effect of --enforce-checksums"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0-gompi-2018a-test.eb')
        test_ec = os.path.join(self.test_prefix, 'test.eb')

        # wipe $EASYBUILD_ROBOT_PATHS to avoid that checksums.json for toy is found in test_ecs
        del os.environ['EASYBUILD_ROBOT_PATHS']

        args = [
            test_ec,
            '--stop=fetch',
            '--enforce-checksums',
        ]

        # checksum is missing for patch of 'bar' extension, so --enforce-checksums should result in an error
        copy_file(toy_ec, test_ec)
        error_pattern = r"Missing checksum for bar-0.0[^ ]*\.patch"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

        # get rid of checksums for extensions, should result in different error message
        # because of missing checksum for source of 'bar' extension
        regex = re.compile("^.*'checksums':.*$", re.M)
        test_ec_txt = regex.sub('', read_file(test_ec))
        self.assertNotIn("'checksums':", test_ec_txt)
        write_file(test_ec, test_ec_txt)
        error_pattern = r"Missing checksum for bar-0\.0\.tar\.gz"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

        # wipe both exts_list and checksums, so we can check whether missing checksum for main source is caught
        test_ec_txt = read_file(test_ec)
        for param in ['checksums', 'exts_list']:
            regex = re.compile(r'^%s(?:.|\n)*?\]\s*$' % param, re.M)
            test_ec_txt = regex.sub('', test_ec_txt)
            self.assertNotIn('%s = ' % param, test_ec_txt)

        write_file(test_ec, test_ec_txt)
        error_pattern = "Missing checksum for toy-0.0.tar.gz"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)

    def test_show_system_info(self):
        """Test for --show-system-info."""
        txt, _ = self._run_mock_eb(['--show-system-info'], raise_error=True)
        patterns = [
            r"^System information \(.*\):$",
            r"^\* OS:$",
            r"^  -> name: ",
            r"^  -> type: ",
            r"^  -> version: ",
            r"^  -> platform name: ",
            r"^\* CPU:$",
            r"^  -> vendor: ",
            r"^  -> architecture: ",
            r"^  -> family: ",
            r"^  -> model: ",
            r"^  -> speed: [0-9.]+",
            r"^  -> cores: [0-9]+",
            r"^  -> features: ",
            r"^\* software:$",
            r"^  -> glibc version: ",
            r"^  -> Python binary: .*/[pP]ython[0-9]?",
            r"^  -> Python version: [0-9.]+",
        ]

        if HAVE_ARCHSPEC:
            patterns.append(r"^  -> arch name: \w+$")
        else:
            patterns.append(r"^  -> arch name: UNKNOWN \(archspec is not installed\?\)$")

        self._assert_regexs(patterns, txt)

    def test_check_eb_deps(self):
        """Test for --check-eb-deps."""
        txt, _ = self._run_mock_eb(['--check-eb-deps'], raise_error=True)

        # keep in mind that these patterns should match with both normal output and Rich output!
        opt_dep_info_pattern = r'([0-9.]+|\(NOT FOUND\)|not found|\(unknown version\))'
        tool_info_pattern = r'([0-9.]+|\(NOT FOUND\)|not found|\(found, UNKNOWN version\)|version\?\!)'
        patterns = [
            r"Required dependencies",
            r"Python.* [23][0-9.]+",
            r"modules tool.* [A-Za-z0-9.\s-]+",
            r"Optional dependencies",
            r"archspec.* %s.*determining name" % opt_dep_info_pattern,
            r"GitPython.* %s.*GitHub integration" % opt_dep_info_pattern,
            r"Rich.* %s.*eb command rich terminal output" % opt_dep_info_pattern,
            r"setuptools.* %s.*information on Python packages" % opt_dep_info_pattern,
            r"System tools",
            r"make.* %s" % tool_info_pattern,
            r"patch.* %s" % tool_info_pattern,
            r"sed.* %s" % tool_info_pattern,
            r"Slurm.* %s" % tool_info_pattern,
        ]

        self._assert_regexs(patterns, txt)

    def test_tmp_logdir(self):
        """Test use of --tmp-logdir."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # purposely use a non-existing directory as log directory
        tmp_logdir = os.path.join(self.test_prefix, 'tmp-logs')
        self.assertNotExists(tmp_logdir)

        # force passing logfile=None to main in eb_main
        self.logfile = None

        # check log message with --skip for existing module
        args = [
            toy_ec,
            '--force',
            '--debug',
            '--tmp-logdir=%s' % tmp_logdir,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)

        tmp_logs = os.listdir(tmp_logdir)
        self.assertEqual(len(tmp_logs), 1)

        logtxt = read_file(os.path.join(tmp_logdir, tmp_logs[0]))
        self.assertIn("COMPLETED: Installation ended successfully", logtxt)

    def test_sanity_check_only(self):
        """Test use of --sanity-check-only."""
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        test_ec = os.path.join(self.test_prefix, 'test.ec')
        test_ec_txt = read_file(toy_ec)
        test_ec_txt += '\n' + '\n'.join([
            "sanity_check_commands = ['barbar', 'toy']",
            "sanity_check_paths = {'files': ['bin/barbar', 'bin/toy'], 'dirs': ['bin']}",
            "exts_defaultclass = 'DummyExtension'",
            "exts_list = [",
            "    ('barbar', '0.0', {",
            "        'start_dir': 'src',",
            "        'exts_filter': ('ls -l lib/lib%(ext_name)s.a', ''),",
            "    })",
            "]",
        ])
        write_file(test_ec, test_ec_txt)

        # sanity check fails if software was not installed yet
        with self.mocked_stdout_stderr():
            outtxt, error_thrown = self.eb_main([test_ec, '--sanity-check-only'], do_build=True, return_error=True)
        self.assertIn("Sanity check failed", str(error_thrown))

        # actually install, then try --sanity-check-only again;
        # need to use --force to install toy because module already exists (but installation doesn't)
        with self.mocked_stdout_stderr():
            self.eb_main([test_ec, '--force'], do_build=True, raise_error=True)

        args = [test_ec, '--sanity-check-only']

        stdout = self.mocked_main(args + ['--trace'], do_build=True, raise_error=True, testing=False)

        skipped = [
            "fetching files and verifying checksums",
            "creating build dir, resetting environment",
            "unpacking",
            "patching",
            "preparing",
            "configuring",
            "building",
            "testing",
            "installing",
            "taking care of extensions",
            "restore after iterating",
            "postprocessing",
            "cleaning up",
            "creating module",
            "permissions",
            "packaging"
        ]
        for skip in skipped:
            self.assertTrue("== %s [skipped]" % skip)

        self.assertIn("== sanity checking...", stdout)
        self.assertIn("COMPLETED: Installation ended successfully", stdout)
        msgs = [
            "  >> file 'bin/barbar' found: OK",
            "  >> file 'bin/toy' found: OK",
            "  >> (non-empty) directory 'bin' found: OK",
            "  >> loading modules: toy/0.0...",
            "  >> result for command 'toy': OK",
            "ls -l lib/libbarbar.a",  # sanity check for extension barbar (via exts_filter)
        ]
        for msg in msgs:
            self.assertIn(msg, stdout)

        ebroottoy = os.path.join(self.test_installpath, 'software', 'toy', '0.0')

        # check if sanity check for extension fails if a file provided by that extension,
        # which is checked by the sanity check for that extension, is no longer there
        libbarbar = os.path.join(ebroottoy, 'lib', 'libbarbar.a')
        move_file(libbarbar, libbarbar + '.moved')

        with self.mocked_stdout_stderr():
            outtxt, error_thrown = self.eb_main(args + ['--debug'], do_build=True, return_error=True)
        error_msg = str(error_thrown)
        error_patterns = [
            r"Sanity check failed",
            r'command "ls -l lib/libbarbar\.a" failed',
        ]
        self._assert_regexs(error_patterns, error_msg)

        # failing sanity check for extension can be bypassed via --skip-extensions
        with self.mocked_stdout_stderr():
            outtxt = self.eb_main(args + ['--skip-extensions'], do_build=True, raise_error=True)
        self.assertIn("Sanity check for toy successful", outtxt)

        # restore fail, we want a passing sanity check for the next check
        move_file(libbarbar + '.moved', libbarbar)

        # check use of --sanity-check-only when installation directory is read-only;
        # cfr. https://github.com/easybuilders/easybuild-framework/issues/3757
        adjust_permissions(ebroottoy, stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH, add=False, recursive=True)

        stdout = self.mocked_main(args + ['--trace'], do_build=True, raise_error=True, testing=False)

        # check whether %(builddir)s value is correct
        # when buildininstalldir is enabled in easyconfig and --sanity-check-only is used
        # (see https://github.com/easybuilders/easybuild-framework/issues/3895)
        test_ec_txt += '\n' + '\n'.join([
            "buildininstalldir = True",
            "sanity_check_commands = [",
            # build and install directory should be the same path
            "    'test %(builddir)s = %(installdir)s',",
            # build/install directory must exist (even though step that creates build dir was never run)
            "    'test -d %(builddir)s',",
            "]",
        ])
        write_file(test_ec, test_ec_txt)
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)

        # also check when using easyblock that enables build_in_installdir in its constructor
        test_ebs = os.path.join(topdir, 'sandbox', 'easybuild', 'easyblocks')
        toy_eb = os.path.join(test_ebs, 't', 'toy.py')
        toy_eb_txt = read_file(toy_eb)

        self.assertNotIn('self.build_in_installdir = True', toy_eb_txt)

        regex = re.compile(r'^(\s+)(super\(\).__init__.*)\n', re.M)
        toy_eb_txt = regex.sub(r'\1\2\n\1self.build_in_installdir = True', toy_eb_txt)
        self.assertIn('self.build_in_installdir = True', toy_eb_txt)

        toy_eb = os.path.join(self.test_prefix, 'toy.py')
        write_file(toy_eb, toy_eb_txt)

        test_ec_txt = test_ec_txt.replace('buildininstalldir = True', '')
        write_file(test_ec, test_ec_txt)

        orig_local_sys_path = sys.path[:]
        args.append('--include-easyblocks=%s' % toy_eb)
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)

        # undo import of the toy easyblock, to avoid problems with other tests
        del sys.modules['easybuild.easyblocks.toy']
        sys.path = orig_local_sys_path
        import easybuild.easyblocks
        reload(easybuild.easyblocks)
        import easybuild.easyblocks.toy
        reload(easybuild.easyblocks.toy)
        # need to reload toy_extension, which imports EB_toy, to ensure right EB_toy is picked up in later tests
        import easybuild.easyblocks.generic.toy_extension
        reload(easybuild.easyblocks.generic.toy_extension)

    def test_skip_extensions(self):
        """Test use of --skip-extensions."""
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        # add extension, which should be skipped
        test_ec = os.path.join(self.test_prefix, 'test.ec')
        test_ec_txt = read_file(toy_ec)
        test_ec_txt += '\n' + '\n'.join([
            "exts_list = [",
            "    ('barbar', '0.0', {",
            "        'start_dir': 'src',",
            "        'exts_filter': ('ls -l lib/lib%(ext_name)s.a', ''),",
            "    })",
            "]",
        ])
        write_file(test_ec, test_ec_txt)

        args = [test_ec, '--force', '--skip-extensions']
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, return_error=True)

        toy_mod = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            toy_mod += '.lua'

        self.assertExists(toy_mod)

        toy_installdir = os.path.join(self.test_installpath, 'software', 'toy', '0.0')
        for path in (os.path.join('bin', 'barbar'), os.path.join('lib', 'libbarbar.a')):
            path = os.path.join(toy_installdir, path)
            self.assertNotExists(path)

    def test_fake_vsc_include(self):
        """Test whether fake 'vsc' namespace is triggered for modules included via --include-*."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        test_mns = os.path.join(self.test_prefix, 'test_mns.py')
        test_mns_txt = '\n'.join([
            "import vsc",
            "from easybuild.tools.module_naming_scheme.easybuild_mns import EasyBuildMNS",
            "class TestMNS(EasyBuildMNS):",
            "    pass",
        ])
        write_file(test_mns, test_mns_txt)

        args = [
            toy_ec,
            '--dry-run',
            '--include-module-naming-schemes=%s' % test_mns,
        ]
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(SystemExit, '1', self.eb_main, args, do_build=True, raise_error=True, verbose=True)
            stderr = self.get_stderr()
        regex = re.compile("ERROR: Detected import from 'vsc' namespace in .*/test_mns.py")
        self.assertRegex(stderr, regex)

    def test_installdir(self):
        """Check naming scheme of installation directory."""

        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        eb = EasyBlock(EasyConfig(toy_ec))
        self.assertTrue(eb.installdir.endswith('/software/toy/0.0'))

        # even with HierarchicalMNS the installation directory remains the same,
        # due to --fixed-installdir-naming-scheme being enabled by default
        args = ['--module-naming-scheme=HierarchicalMNS']
        init_config(args=args)
        eb = EasyBlock(EasyConfig(toy_ec))
        self.assertTrue(eb.installdir.endswith('/software/toy/0.0'))

        # things change when --disable-fixed-installdir-naming-scheme is used
        init_config(args=args, build_options={'fixed_installdir_naming_scheme': False})
        eb = EasyBlock(EasyConfig(toy_ec))
        self.assertTrue(eb.installdir.endswith('/software/Core/toy/0.0'))

    def test_cuda_compute_capabilities(self):
        """Test --cuda-compute-capabilities configuration option."""
        args = ['--cuda-compute-capabilities=3.5,6.2,7.0', '--show-config']
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        regex = re.compile(r"^cuda-compute-capabilities\s*\(C\)\s*=\s*3\.5, 6\.2, 7\.0$", re.M)
        self.assertRegex(txt, regex)

    def test_create_index(self):
        """Test --create-index option."""
        test_ecs = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'easyconfigs', 'test_ecs')
        remove_dir(self.test_prefix)
        copy_dir(test_ecs, self.test_prefix)

        args = ['--create-index', self.test_prefix]
        stdout, stderr = self._run_mock_eb(args, raise_error=True)

        self.assertEqual(stderr, '')

        patterns = [p % self.test_prefix for p in (
            r"^Creating index for %s\.\.\.$",
            r"^Index created at %s/\.eb-path-index \([0-9]+ files\)$",
        )]
        self._assert_regexs(patterns, stdout)

        # check contents of index
        index_fp = os.path.join(self.test_prefix, '.eb-path-index')
        index_txt = read_file(index_fp)

        datestamp_pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+"
        patterns = [
            r"^# created at: " + datestamp_pattern + '$',
            r"^# valid until: " + datestamp_pattern + '$',
            r"^g/GCC/GCC-7.3.0-2.30.eb",
            r"^t/toy/toy-0\.0\.eb",
        ]
        self._assert_regexs(patterns, index_txt)

        # existing index is not overwritten without --force
        error_pattern = "File exists, not overwriting it without --force: .*/.eb-path-index"
        self.assertErrorRegex(EasyBuildError, error_pattern, self._run_mock_eb, args, raise_error=True)

        # also test creating index that's infinitely valid
        args.extend(['--index-max-age=0', '--force'])
        self._run_mock_eb(args, raise_error=True)
        index_txt = read_file(index_fp)
        regex = re.compile(r"^# valid until: 9999-12-31 23:59:59", re.M)
        self.assertRegex(index_txt, regex)

    def test_sysroot(self):
        """Test use of --sysroot option."""

        self.assertExists(self.test_prefix)

        sysroot_arg = '--sysroot=' + self.test_prefix
        stdout, stderr = self._run_mock_eb([sysroot_arg, '--show-config'], raise_error=True)

        self.assertEqual(stderr, '')
        sysroot_regex = re.compile(r'^sysroot\s*\(C\) = %s$' % self.test_prefix, re.M)
        self.assertRegex(stdout, sysroot_regex)

        os.environ['EASYBUILD_SYSROOT'] = self.test_prefix
        stdout, stderr = self._run_mock_eb(['--show-config'], raise_error=True)

        self.assertEqual(stderr, '')
        sysroot_regex = re.compile(r'^sysroot\s*\(E\) = %s$' % self.test_prefix, re.M)
        self.assertRegex(stdout, sysroot_regex)

        # specifying a non-existing path results in an error
        doesnotexist = os.path.join(self.test_prefix, 'non-existing-subdirectory')
        sysroot_arg = '--sysroot=' + doesnotexist

        args = [sysroot_arg, '--show-config']
        error_pattern = r"Specified sysroot '%s' does not exist!" % doesnotexist
        self.assertErrorRegex(EasyBuildError, error_pattern, self._run_mock_eb, args, raise_error=True)

        os.environ['EASYBUILD_SYSROOT'] = doesnotexist
        self.assertErrorRegex(EasyBuildError, error_pattern, self._run_mock_eb, ['--show-config'], raise_error=True)

    def test_software_commit(self):
        """Test use of --software-commit option."""

        software_commit = "23be34"
        software_commit_arg = '--software-commit=' + software_commit
        # Add robot to also test that it gets disabled
        stdout, stderr = self._run_mock_eb([software_commit_arg, '--show-config', '--robot'], raise_error=True)

        warning_regex = re.compile(r'.*WARNING:.*--software-commit robot resolution is being disabled.*', re.M)
        software_commit_regex = re.compile(r'^software-commit\s*\(C\) = %s$' % software_commit, re.M)
        robot_regex = re.compile(r'^robot\s*\(C\) = .*', re.M)

        self.assertRegex(stderr, warning_regex)
        self.assertRegex(stdout, software_commit_regex)
        self.assertNotRegex(stdout, robot_regex)

    def test_accept_eula_for(self):
        """Test --accept-eula-for configuration option."""

        # use toy-0.0.eb easyconfig file that comes with the tests
        topdir = os.path.abspath(os.path.dirname(__file__))
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        test_ec = os.path.join(self.test_prefix, 'test.eb')
        test_ec_txt = '\n'.join([
            "easyblock = 'EB_toy_eula'",
            '',
            read_file(toy_ec),
        ])
        write_file(test_ec, test_ec_txt)

        # by default, no EULAs are accepted at all
        args = [test_ec, '--force']
        error_pattern = r"The End User License Agreement \(EULA\) for toy is currently not accepted!"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error_pattern, self.eb_main, args, do_build=True, raise_error=True)
        toy_modfile = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            toy_modfile += '.lua'

        # installation proceeds if EasyBuild is configured to accept EULA for specified software via --accept-eula-for
        for val in ('foo,toy,bar', '.*', 't.y'):
            with self.mocked_stdout_stderr():
                self.eb_main(args + ['--accept-eula-for=' + val], do_build=True, raise_error=True)

            self.assertExists(toy_modfile)

            remove_dir(self.test_installpath)
            self.assertNotExists(toy_modfile)

            # also check use of $EASYBUILD_ACCEPT_EULA to accept EULA for specified software
            os.environ['EASYBUILD_ACCEPT_EULA_FOR'] = val
            with self.mocked_stdout_stderr():
                self.eb_main(args, do_build=True, raise_error=True)
            self.assertExists(toy_modfile)

            remove_dir(self.test_installpath)
            self.assertNotExists(toy_modfile)

            del os.environ['EASYBUILD_ACCEPT_EULA_FOR']

        # also check accepting EULA via 'accept_eula = True' in easyconfig file
        write_file(test_ec, test_ec_txt + '\naccept_eula = True')
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True)
        self.assertExists(toy_modfile)

    def test_config_abs_path(self):
        """Test ensuring of absolute path values for path configuration options."""

        test_topdir = os.path.join(self.test_prefix, 'test_topdir')
        test_subdir = os.path.join(test_topdir, 'test_middle_dir', 'test_subdir')
        mkdir(test_subdir, parents=True)
        change_dir(test_subdir)

        # a relative path specified in a configuration file is positively weird, but fine :)
        cfgfile = os.path.join(self.test_prefix, 'test.cfg')
        cfgtxt = '\n'.join([
            "[config]",
            "containerpath = ..",
            "repositorypath = /apps/easyconfigs_archive, somesubdir",
        ])
        write_file(cfgfile, cfgtxt)

        # relative paths in environment variables is also weird,
        # but OK for the sake of testing...
        os.environ['EASYBUILD_INSTALLPATH'] = '../..'
        os.environ['EASYBUILD_ROBOT_PATHS'] = '../..'

        args = [
            '--configfiles=%s' % cfgfile,
            '--prefix=..',
            '--sourcepath=.',
            '--show-config',
        ]

        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        patterns = [
            r"^containerpath\s+\(F\) = /.*/test_topdir/test_middle_dir$",
            r"^installpath\s+\(E\) = /.*/test_topdir$",
            r"^prefix\s+\(C\) = /.*/test_topdir/test_middle_dir$",
            r"^repositorypath\s+\(F\) = /apps/easyconfigs_archive,\s+somesubdir$",
            r"^sourcepath\s+\(C\) = /.*/test_topdir/test_middle_dir/test_subdir$",
            r"^robot-paths\s+\(E\) = /.*/test_topdir$",
        ]
        self._assert_regexs(patterns, txt)

        # paths specified via --robot have precedence over those specified via $EASYBUILD_ROBOT_PATHS
        change_dir(test_subdir)
        args.append('--robot=..:.')
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        patterns.pop(-1)
        robot_value_pattern = ', '.join([
            r'/.*/test_topdir/test_middle_dir',  # via --robot (first path)
            r'/.*/test_topdir/test_middle_dir/test_subdir',  # via --robot (second path)
            r'/.*/test_topdir',  # via $EASYBUILD_ROBOT_PATHS
        ])
        patterns.extend([
            r"^robot-paths\s+\(C\) = %s$" % robot_value_pattern,
            r"^robot\s+\(C\) = %s$" % robot_value_pattern,
        ])
        self._assert_regexs(patterns, txt)

    def test_config_repositorypath(self):
        """Test how special repositorypath values are handled."""

        repositorypath = 'git@github.com:boegel/my_easyconfigs.git'
        args = [
            '--repositorypath=%s' % repositorypath,
            '--show-config',
        ]
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        regex = re.compile(r'repositorypath\s+\(C\) = %s' % repositorypath, re.M)
        self.assertRegex(txt, regex)

        args[0] = '--repositorypath=%s,some/subdir' % repositorypath
        txt, _ = self._run_mock_eb(args, do_build=True, raise_error=True, testing=False, strip=True)

        regex = re.compile(r"repositorypath\s+\(C\) = %s, some/subdir" % repositorypath, re.M)
        self.assertRegex(txt, regex)

    # end-to-end testing of unknown filename
    def test_easystack_wrong_read(self):
        """Test for --easystack <easystack.yaml> when wrong name is provided"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_easystack = os.path.join(topdir, 'easystacks', 'test_easystack_nonexistent.yaml')
        args = ['--easystack', toy_easystack, '--experimental']
        expected_err = "No such file or directory: '%s'" % toy_easystack
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, expected_err, self.eb_main, args, raise_error=True)

    # testing basics - end-to-end
    # expecting successful build
    def test_easystack_basic(self):
        """Test for --easystack <easystack.yaml> -> success case"""
        topdir = os.path.dirname(os.path.abspath(__file__))
        toy_easystack = os.path.join(topdir, 'easystacks', 'test_easystack_basic.yaml')

        args = ['--easystack', toy_easystack, '--debug', '--experimental', '--dry-run']
        with self.mocked_stdout_stderr():
            stdout = self.eb_main(args, do_build=True, raise_error=True)
        patterns = [
            r"INFO Building from easystack:",
            r"DEBUG Parsed easystack:\n"
            ".*binutils-2.25-GCCcore-4.9.3.eb.*\n"
            ".*binutils-2.26-GCCcore-4.9.3.eb.*\n"
            ".*foss-2018a.eb.*\n"
            ".*toy-0.0-gompi-2018a-test.eb.*",
            r"\* \[ \] .*/test_ecs/b/binutils/binutils-2.25-GCCcore-4.9.3.eb \(module: binutils/2.25-GCCcore-4.9.3\)",
            r"\* \[ \] .*/test_ecs/b/binutils/binutils-2.26-GCCcore-4.9.3.eb \(module: binutils/2.26-GCCcore-4.9.3\)",
            r"\* \[ \] .*/test_ecs/t/toy/toy-0.0-gompi-2018a-test.eb \(module: toy/0.0-gompi-2018a-test\)",
            r"\* \[x\] .*/test_ecs/f/foss/foss-2018a.eb \(module: foss/2018a\)",
        ]
        self._assert_regexs(patterns, stdout)

    def test_easystack_opts(self):
        """Test for easystack file that specifies options for specific easyconfigs."""

        robot_paths = os.environ['EASYBUILD_ROBOT_PATHS']
        hidden_installpath = os.path.join(self.test_installpath, 'hidden')

        test_es_txt = '\n'.join([
            "easyconfigs:",
            "  - toy-0.0:",
            "      options:",
            "        force: True",
            "        hidden: True",
            "        installpath: %s" % hidden_installpath,
            "  - libtoy-0.0:",
            "      options:",
            "        force: True",
            "        robot: ~",
            "        robot-paths: %s:%s" % (robot_paths, self.test_prefix),
        ])
        test_es_path = os.path.join(self.test_prefix, 'test.yml')
        write_file(test_es_path, test_es_txt)

        mod_dir = os.path.join(self.test_installpath, 'modules', 'all')

        # touch module file for libtoy, so we can check whether the existing module is replaced
        libtoy_mod = os.path.join(mod_dir, 'libtoy', '0.0')
        write_file(libtoy_mod, ModuleGeneratorTcl.MODULE_SHEBANG)

        del os.environ['EASYBUILD_INSTALLPATH']
        args = [
            '--experimental',
            '--easystack', test_es_path,
            '--installpath', self.test_installpath,
        ]
        with self.mocked_stdout_stderr():
            self.eb_main(args, do_build=True, raise_error=True, redo_init_config=False)

        mod_ext = '.lua' if get_module_syntax() == 'Lua' else ''

        # make sure that $EBROOTLIBTOY is not defined
        os.environ.pop('EBROOTLIBTOY', None)

        # libtoy module should be installed, module file should at least set EBROOTLIBTOY
        mod_dir = os.path.join(self.test_installpath, 'modules', 'all')
        mod_path = os.path.join(mod_dir, 'libtoy', '0.0') + mod_ext
        self.assertExists(mod_path)
        self.modtool.use(mod_dir)
        self.modtool.load(['libtoy'])
        self.assertExists(os.environ['EBROOTLIBTOY'])

        # module should be hidden and in different install path
        mod_path = os.path.join(hidden_installpath, 'modules', 'all', 'toy', '.0.0') + mod_ext
        self.assertExists(mod_path)

        # check build options that were put in place for last easyconfig
        self.assertFalse(build_option('hidden'))
        self.assertTrue(build_option('force'))
        self.assertEqual(build_option('robot'), [robot_paths, self.test_prefix])

    def test_easystack_easyconfigs_cache(self):
        """
        Test for easystack file that specifies same easyconfig twice,
        but from a different location.
        """
        topdir = os.path.abspath(os.path.dirname(__file__))
        libtoy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'l', 'libtoy', 'libtoy-0.0.eb')
        toy_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')

        test_ec = os.path.join(self.test_prefix, 'toy-0.0.eb')
        test_ec_txt = read_file(toy_ec)
        test_ec_txt += "\ndependencies = [('libtoy', '0.0')]"
        write_file(test_ec, test_ec_txt)

        test_subdir = os.path.join(self.test_prefix, 'deps')
        mkdir(test_subdir, parents=True)
        copy_file(libtoy_ec, test_subdir)

        test_es_txt = '\n'.join([
            "easyconfigs:",
            "  - toy-0.0",
            "  - toy-0.0:",
            "      options:",
            "        robot: %s:%s" % (test_subdir, self.test_prefix),
        ])
        test_es_path = os.path.join(self.test_prefix, 'test.yml')
        write_file(test_es_path, test_es_txt)

        args = [
            '--experimental',
            '--easystack', test_es_path,
            '--dry-run',
            '--robot=%s' % self.test_prefix,
        ]
        with self.mocked_stdout_stderr():
            stdout = self.eb_main(args, do_build=True, raise_error=True, redo_init_config=False)

        # check whether libtoy-0.0.eb comes from 2nd
        regex = re.compile(r"^ \* \[ \] %s" % libtoy_ec, re.M)
        self.assertRegex(stdout, regex)

        regex = re.compile(r"^ \* \[ \] %s" % os.path.join(test_subdir, 'libtoy-0.0.eb'), re.M)
        self.assertRegex(stdout, regex)

    def test_set_up_configuration(self):
        """Tests for set_up_configuration function."""

        # check default configuration first
        self.assertFalse(build_option('debug'))
        self.assertFalse(build_option('hidden'))
        # tests may be configured to run with Tcl module syntax
        self.assertIn(get_module_syntax(), ('Lua', 'Tcl'))

        # start with a clean slate, reset all configuration done by setUp method that prepares each test
        cleanup()

        os.environ['EASYBUILD_PREFIX'] = self.test_prefix
        eb_go, settings = set_up_configuration(args=['--debug', '--module-syntax=Tcl'], silent=True)

        # 2nd part of return value is a tuple with various settings
        self.assertIsInstance(settings, tuple)
        self.assertEqual(len(settings), 9)
        self.assertEqual(settings[0], {})  # build specs
        self.assertIsInstance(settings[1], EasyBuildLog)  # EasyBuildLog instance
        self.assertTrue(settings[2].endswith('.log'))  # path to log file
        self.assertExists(settings[2])
        self.assertIsInstance(settings[3], list)  # list of robot paths
        self.assertEqual(len(settings[3]), 1)
        self.assertTrue(os.path.samefile(settings[3][0], os.environ['EASYBUILD_ROBOT_PATHS']))
        self.assertEqual(settings[4], None)  # search query
        self.assertTrue(os.path.samefile(settings[5], tempfile.gettempdir()))  # tmpdir
        self.assertEqual(settings[6], False)  # try_to_generate
        self.assertEqual(settings[7], [])  # from_prs list
        self.assertEqual(settings[8], None)  # list of paths for tweaked ecs

        self.assertEqual(eb_go.options.prefix, self.test_prefix)
        self.assertTrue(eb_go.options.debug)
        self.assertEqual(eb_go.options.module_syntax, 'Tcl')

        # set_up_configuration also initializes build options and configuration variables (both Singleton classes)
        self.assertTrue(build_option('debug'))
        self.assertTrue(BuildOptions()['debug'])

        self.assertEqual(ConfigurationVariables()['module_syntax'], 'Tcl')
        self.assertEqual(get_module_syntax(), 'Tcl')

        self.assertFalse(BuildOptions()['hidden'])
        self.assertFalse(build_option('hidden'))

        # calling set_up_configuration again triggers a warning being printed,
        # because build options and configuration variables will not be re-configured by default!
        with self.mocked_stdout_stderr():
            eb_go, _ = set_up_configuration(args=['--hidden'], silent=True)
            stderr = self.get_stderr()

        self.assertIn("WARNING: set_up_configuration is about to call init() and init_build_options()", stderr)

        # 'hidden' option is enabled, but corresponding build option is still set to False!
        self.assertTrue(eb_go.options.hidden)
        self.assertFalse(BuildOptions()['hidden'])
        self.assertFalse(build_option('hidden'))

        self.assertEqual(eb_go.options.prefix, self.test_prefix)

        self.assertTrue(build_option('debug'))
        self.assertTrue(BuildOptions()['debug'])

        self.assertEqual(ConfigurationVariables()['module_syntax'], 'Tcl')
        self.assertEqual(get_module_syntax(), 'Tcl')

        # build options and configuration variables are only re-initialized on demand
        eb_go, _ = set_up_configuration(args=['--hidden'], silent=True, reconfigure=True)

        self.assertTrue(eb_go.options.hidden)
        self.assertTrue(BuildOptions()['hidden'])
        self.assertTrue(build_option('hidden'))

        self.assertEqual(eb_go.options.prefix, self.test_prefix)

        self.assertFalse(build_option('debug'))
        self.assertFalse(BuildOptions()['debug'])

        # tests may be configured to run with Tcl module syntax
        self.assertIn(ConfigurationVariables()['module_syntax'], ('Lua', 'Tcl'))
        self.assertIn(get_module_syntax(), ('Lua', 'Tcl'))

    def test_opts_dict_to_eb_opts(self):
        """Tests for opts_dict_to_eb_opts."""

        self.assertEqual(opts_dict_to_eb_opts({}), [])
        self.assertEqual(opts_dict_to_eb_opts({'foo': '123'}), ['--foo=123'])

        opts_dict = {
            'module-syntax': 'Tcl',
            # multi-value option
            'from-pr': [1234, 2345],
            # enabled boolean options
            'robot': None,
            'force': True,
            # disabled boolean option
            'debug': False,
        }
        expected = [
            '--disable-debug',
            '--force',
            '--from-pr=1234,2345',
            '--module-syntax=Tcl',
            '--robot',
        ]
        self.assertEqual(opts_dict_to_eb_opts(opts_dict), expected)

        # multi-call options
        opts_dict = {'try-amend': ['a=1', 'b=2', 'c=3']}
        expected = ['--try-amend=a=1', '--try-amend=b=2', '--try-amend=c=3']
        self.assertEqual(opts_dict_to_eb_opts(opts_dict), expected)

        opts_dict = {'amend': ['a=1', 'b=2', 'c=3']}
        expected = ['--amend=a=1', '--amend=b=2', '--amend=c=3']
        self.assertEqual(opts_dict_to_eb_opts(opts_dict), expected)


def suite(loader=None):
    """ returns all the testcases in this module """
    if loader:
        return loader.loadTestsFromTestCase(CommandLineOptionsTest)
    else:
        return TestLoaderFiltered().loadTestsFromTestCase(CommandLineOptionsTest, sys.argv[1:])


if __name__ == '__main__':
    res = TextTestRunner(verbosity=1).run(suite())
    sys.exit(len(res.failures))
