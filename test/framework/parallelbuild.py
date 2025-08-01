# #
# Copyright 2014-2025 Ghent University
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
Unit tests for parallelbuild.py

@author: Kenneth Hoste (Ghent University)
"""
import os
import re
import stat
import sys
from test.framework.utilities import EnhancedTestCase, TestLoaderFiltered, init_config
from unittest import TextTestRunner

from easybuild.framework.easyconfig.tools import process_easyconfig
from easybuild.tools import config
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.config import get_module_syntax, update_build_option
from easybuild.tools.filetools import adjust_permissions, mkdir, read_file, remove_dir, which, write_file
from easybuild.tools.job import pbs_python
from easybuild.tools.job.pbs_python import PbsPython
from easybuild.tools.options import parse_options
from easybuild.tools.parallelbuild import build_easyconfigs_in_parallel, submit_jobs
from easybuild.tools.robot import resolve_dependencies


# test GC3Pie configuration with large resource specs
GC3PIE_LOCAL_CONFIGURATION = """[resource/ebtestlocalhost]
enabled = yes
type = shellcmd
frontend = localhost
transport = local
max_cores_per_job = 1
max_memory_per_core = 1000GiB
max_walltime = 1000 hours
# this doubles as "maximum concurrent jobs"
max_cores = 1000
architecture = x86_64
auth = none
override = no
resourcedir = %(resourcedir)s
time_cmd = %(time)s
"""


MOCKED_SBATCH = """#!/bin/bash
if [[ $1 == '--version' ]]; then
    echo "slurm 17.0"
else
    echo "Submitted batch job $RANDOM"
    echo "(submission args: $@)"
fi
"""

MOCKED_SCONTROL = """#!/bin/bash
    echo "(scontrol args: $@)"
"""


def mock(*args, **kwargs):
    """Function used for mocking several functions imported in parallelbuild module."""
    return 1


class MockPbsJob:
    """Mocking class for PbsJob."""

    def __init__(self, *args, **kwargs):
        self.deps = []
        self.jobid = None
        self.clean_conn = None
        self.script = args[1]
        self.cores = kwargs['cores']

    def add_dependencies(self, jobs):
        self.deps.extend(jobs)

    def cleanup(self, *args, **kwargs):
        pass

    def has_holds(self, *args, **kwargs):
        pass

    def _submit(self, *args, **kwargs):
        pass


class ParallelBuildTest(EnhancedTestCase):
    """ Testcase for run module """

    def test_build_easyconfigs_in_parallel_pbs_python(self):
        """Test build_easyconfigs_in_parallel(), using (mocked) pbs_python as backend for --job."""
        self.mock_stdout(True)
        # put mocked functions in place
        PbsPython__init__ = PbsPython.__init__
        PbsPython_check_version = PbsPython._check_version
        PbsPython_complete = PbsPython.complete
        PbsPython_connect_to_server = PbsPython.connect_to_server
        PbsPython_ppn = PbsPython.ppn
        pbs_python_PbsJob = pbs_python.PbsJob

        PbsPython.__init__ = lambda self: PbsPython__init__(self, pbs_server='localhost')
        PbsPython._check_version = lambda _: True
        PbsPython.complete = mock
        PbsPython.connect_to_server = mock
        PbsPython.ppn = mock
        pbs_python.PbsJob = MockPbsJob

        topdir = os.path.dirname(os.path.abspath(__file__))

        build_options = {
            'external_modules_metadata': {},
            'robot_path': os.path.join(topdir, 'easyconfigs', 'test_ecs'),
            'valid_module_classes': config.module_classes(),
            'validate': False,
            'job_cores': 3,
        }
        init_config(args=['--job-backend=PbsPython'], build_options=build_options)

        ec_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'g', 'gzip', 'gzip-1.5-foss-2018a.eb')
        easyconfigs = process_easyconfig(ec_file)
        ordered_ecs = resolve_dependencies(easyconfigs, self.modtool)
        jobs = build_easyconfigs_in_parallel("echo '%(spec)s'", ordered_ecs, prepare_first=False)
        # only one job submitted since foss/2018a module is already available
        self.assertEqual(len(jobs), 1)
        regex = re.compile("echo '.*/gzip-1.5-foss-2018a.eb'")
        self.assertTrue(regex.search(jobs[-1].script), "Pattern '%s' found in: %s" % (regex.pattern, jobs[-1].script))

        ec_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'g', 'gzip', 'gzip-1.4-GCC-4.6.3.eb')
        ordered_ecs = resolve_dependencies(process_easyconfig(ec_file), self.modtool, retain_all_deps=True)
        jobs = submit_jobs(ordered_ecs, '', testing=False, prepare_first=False)

        # make sure command is correct, and that --hidden is there when it needs to be
        for i, ec in enumerate(ordered_ecs):
            if ec['hidden']:
                regex = re.compile("eb %s.* --hidden" % ec['spec'])
            else:
                regex = re.compile("eb %s" % ec['spec'])
            self.assertTrue(regex.search(jobs[i].script), "Pattern '%s' found in: %s" % (regex.pattern, jobs[i].script))

        for job in jobs:
            self.assertEqual(job.cores, build_options['job_cores'])

        # no deps for GCC/4.6.3 (toolchain) and intel/2018a (test easyconfig with 'fake' deps)
        self.assertEqual(len(jobs[0].deps), 0)
        self.assertEqual(len(jobs[1].deps), 0)

        # only dependency for toy/0.0-deps is intel/2018a (dep marked as external module is filtered out)
        self.assertIn('toy-0.0-deps.eb', jobs[2].script)
        self.assertEqual(len(jobs[2].deps), 1)
        self.assertIn('intel-2018a.eb', jobs[2].deps[0].script)

        # dependencies for gzip/1.4-GCC-4.6.3: GCC/4.6.3 (toolchain) + toy/.0.0-deps
        self.assertIn('gzip-1.4-GCC-4.6.3.eb', jobs[3].script)
        self.assertEqual(len(jobs[3].deps), 2)
        regex = re.compile(r'toy-0.0-deps\.eb.* --hidden')
        script_txt = jobs[3].deps[0].script
        fail_msg = "Pattern '%s' should be found in: %s" % (regex.pattern, script_txt)
        self.assertTrue(regex.search(script_txt), fail_msg)
        self.assertIn('GCC-4.6.3.eb', jobs[3].deps[1].script)

        # also test use of --pre-create-installdir
        ec_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        ordered_ecs = resolve_dependencies(process_easyconfig(ec_file), self.modtool)

        # installation directory doesn't exist yet before submission
        toy_installdir = os.path.join(self.test_installpath, 'software', 'toy', '0.0')
        self.assertNotExists(toy_installdir)

        jobs = submit_jobs(ordered_ecs, '', testing=False)
        self.assertEqual(len(jobs), 1)

        # software install dir is created (by default) as part of job submission process (fetch_step is run)
        self.assertExists(toy_installdir)
        remove_dir(toy_installdir)
        remove_dir(os.path.dirname(toy_installdir))
        self.assertNotExists(toy_installdir)

        # installation directory does *not* get created when --pre-create-installdir is used
        build_options['pre_create_installdir'] = False
        init_config(args=['--job-backend=PbsPython'], build_options=build_options)

        jobs = submit_jobs(ordered_ecs, '', testing=False)
        self.assertEqual(len(jobs), 1)
        self.assertNotExists(toy_installdir)

        # restore mocked stuff
        PbsPython.__init__ = PbsPython__init__
        PbsPython._check_version = PbsPython_check_version
        PbsPython.complete = PbsPython_complete
        PbsPython.connect_to_server = PbsPython_connect_to_server
        PbsPython.ppn = PbsPython_ppn
        pbs_python.PbsJob = pbs_python_PbsJob
        self.mock_stdout(False)

    def test_build_easyconfigs_in_parallel_gc3pie(self):
        """Test build_easyconfigs_in_parallel(), using GC3Pie with local config as backend for --job."""
        try:
            import gc3libs  # noqa (ignore unused import)
        except ImportError:
            print("GC3Pie not available, skipping test")
            return

        self.allow_deprecated_behaviour()

        # put GC3Pie config in place to use local host and fork/exec
        resourcedir = os.path.join(self.test_prefix, 'gc3pie')
        gc3pie_cfgfile = os.path.join(self.test_prefix, 'gc3pie_local.ini')
        gc3pie_cfgtxt = GC3PIE_LOCAL_CONFIGURATION % {
            'resourcedir': resourcedir,
            'time': which('time'),
        }
        write_file(gc3pie_cfgfile, gc3pie_cfgtxt)

        output_dir = os.path.join(self.test_prefix, 'subdir', 'gc3pie_output_dir')
        # purposely pre-create output dir, and put a file in it (to check whether GC3Pie tries to rename the output dir)
        mkdir(output_dir, parents=True)
        write_file(os.path.join(output_dir, 'foo'), 'bar')
        # remove write permissions on parent dir of specified output dir,
        # to check that GC3Pie does not try to rename the (already existing) output directory...
        adjust_permissions(os.path.dirname(output_dir), stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
                           add=False, recursive=False)

        topdir = os.path.dirname(os.path.abspath(__file__))

        build_options = {
            'job_backend_config': gc3pie_cfgfile,
            'job_max_walltime': 24,
            'job_output_dir': output_dir,
            'job_polling_interval': 0.2,  # quick polling
            'job_target_resource': 'ebtestlocalhost',
            'robot_path': os.path.join(topdir, 'easyconfigs', 'test_ecs'),
            'silent': True,
            'valid_module_classes': config.module_classes(),
            'validate': False,
        }
        init_config(args=['--job-backend=GC3Pie'], build_options=build_options)

        ec_file = os.path.join(topdir, 'easyconfigs', 'test_ecs', 't', 'toy', 'toy-0.0.eb')
        easyconfigs = process_easyconfig(ec_file)
        ordered_ecs = resolve_dependencies(easyconfigs, self.modtool)
        topdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        test_easyblocks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sandbox')
        cmd = "PYTHONPATH=%s:%s:$PYTHONPATH eb %%(spec)s -df" % (topdir, test_easyblocks_path)

        with self.mocked_stdout_stderr():
            build_easyconfigs_in_parallel(cmd, ordered_ecs, prepare_first=False)

        toy_modfile = os.path.join(self.test_installpath, 'modules', 'all', 'toy', '0.0')
        if get_module_syntax() == 'Lua':
            toy_modfile += '.lua'
        self.assertExists(toy_modfile)
        self.assertExists(os.path.join(self.test_installpath, 'software', 'toy', '0.0', 'bin', 'toy'))

        # also check what happens when a job fails (an error should be raised)
        test_ecfile = os.path.join(self.test_prefix, 'test.eb')
        ectxt = read_file(ec_file)
        # use different version, for which no sources are available
        regex = re.compile('^version = .*', re.M)
        ectxt = regex.sub("version = '1.2.3'", ectxt)
        write_file(test_ecfile, ectxt)
        ecs = resolve_dependencies(process_easyconfig(test_ecfile), self.modtool)

        error = "1 jobs failed: toy-1.2.3"
        with self.mocked_stdout_stderr():
            self.assertErrorRegex(EasyBuildError, error, build_easyconfigs_in_parallel, cmd, ecs, prepare_first=False)

    def test_submit_jobs(self):
        """Test submit_jobs"""
        test_easyconfigs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'easyconfigs', 'test_ecs')
        toy_ec = process_easyconfig(os.path.join(test_easyconfigs_dir, 't', 'toy', 'toy-0.0.eb'))

        args = [
            '--debug',
            '--tmpdir', '/tmp',
            '--optarch="GCC:O3 -mtune=generic;Intel:O3 -xHost"',
            '--parallel=2',
            '--try-toolchain=intel,2016a',  # should be excluded in job script
            '--robot', self.test_prefix,  # should be excluded in job script
            '--job',  # should be excluded in job script
            '--job-cores=3',
        ]
        eb_go = parse_options(args=args)
        cmd = submit_jobs(toy_ec, eb_go.generate_cmd_line(), testing=True)

        # these patterns must be found
        regexs = [
            r' --debug ',
            # values got wrapped in single quotes (to avoid interpretation by shell)
            r" --tmpdir='/tmp' ",
            r" --parallel='2' ",
            # (unparsed) optarch value got wrapped in single quotes, double quotes got stripped
            r" --optarch='GCC:O3 -mtune=generic;Intel:O3 -xHost' ",
            # templates to be completed via build_easyconfigs_in_parallel -> create_job
            r' eb %\(spec\)s ',
            r' %\(add_opts\)s ',
            r' --testoutput=%\(output_dir\)s',
            r' --disable-job ',
        ]
        for regex in regexs:
            regex = re.compile(regex)
            self.assertTrue(regex.search(cmd), "Pattern '%s' found in: %s" % (regex.pattern, cmd))

        # these patterns should NOT be found, these options get filtered out
        # (self.test_prefix was argument to --robot)
        for regex in ['--job', '--job-cores', '--try-toolchain', '--robot=[ =]', self.test_prefix + ' ']:
            regex = re.compile(regex)
            self.assertFalse(regex.search(cmd), "Pattern '%s' should *not* be found in: %s" % (regex.pattern, cmd))

        # test again with custom EasyBuild command to use in jobs
        update_build_option('job_eb_cmd', "/just/testing/bin/eb --debug")
        cmd = submit_jobs(toy_ec, eb_go.generate_cmd_line(), testing=True)
        regex = re.compile(r" && /just/testing/bin/eb --debug %\(spec\)s ")
        self.assertTrue(regex.search(cmd), "Pattern '%s' found in: %s" % (regex.pattern, cmd))

    def test_build_easyconfigs_in_parallel_slurm(self):
        """Test build_easyconfigs_in_parallel(), using (mocked) Slurm as backend for --job."""

        # install mocked versions of 'sbatch' and 'scontrol' commands
        sbatch = os.path.join(self.test_prefix, 'bin', 'sbatch')
        write_file(sbatch, MOCKED_SBATCH)
        adjust_permissions(sbatch, stat.S_IXUSR, add=True)

        scontrol = os.path.join(self.test_prefix, 'bin', 'scontrol')
        write_file(scontrol, MOCKED_SCONTROL)
        adjust_permissions(scontrol, stat.S_IXUSR, add=True)

        os.environ['PATH'] = os.path.pathsep.join([os.path.join(self.test_prefix, 'bin'), os.getenv('PATH')])

        topdir = os.path.dirname(os.path.abspath(__file__))
        test_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'g', 'gzip', 'gzip-1.5-foss-2018a.eb')
        foss_ec = os.path.join(topdir, 'easyconfigs', 'test_ecs', 'f', 'foss', 'foss-2018a.eb')

        build_options = {
            'external_modules_metadata': {},
            'robot_path': os.path.join(topdir, 'easyconfigs', 'test_ecs'),
            'valid_module_classes': config.module_classes(),
            'validate': False,
            'job_cores': 3,
            'job_max_walltime': 5,
            'force': True,
        }
        init_config(args=['--job-backend=Slurm'], build_options=build_options)

        easyconfigs = process_easyconfig(test_ec) + process_easyconfig(foss_ec)
        ordered_ecs = resolve_dependencies(easyconfigs, self.modtool)
        self.mock_stdout(True)
        jobs = build_easyconfigs_in_parallel("echo '%(spec)s'", ordered_ecs, prepare_first=False)
        self.mock_stdout(False)

        # jobs are submitted for foss & gzip (listed easyconfigs)
        self.assertEqual(len(jobs), 2)

        # last job (gzip) has a dependency on second-to-last job (foss)
        self.assertEqual(jobs[0].job_specs['job-name'], 'foss-2018a')

        expected = {
            'dependency': 'afterok:%s' % jobs[0].jobid,
            'hold': True,
            'job-name': 'gzip-1.5-foss-2018a',
            'nodes': 1,
            'ntasks': 3,
            'output': 'gzip-1.5-foss-2018a-%j.out',
            'time': 300,  # 60*5 (unit is minutes)
            'wrap': "echo '%s'" % test_ec,
        }
        self.assertEqual(jobs[1].job_specs, expected)


def suite(loader=None):
    """ returns all the testcases in this module """
    if loader:
        return loader.loadTestsFromTestCase(ParallelBuildTest)
    else:
        return TestLoaderFiltered().loadTestsFromTestCase(ParallelBuildTest, sys.argv[1:])


if __name__ == '__main__':
    res = TextTestRunner(verbosity=1).run(suite())
    sys.exit(len(res.failures))
