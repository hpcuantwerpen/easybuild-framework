##
# Copyright 2011-2013 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://vscentrum.be/nl/en),
# the Hercules foundation (http://www.herculesstichting.be/in_English)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
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
##
"""
Module with useful functions for getting system information

@author: Jens Timmerman (Ghent University)
@auther: Ward Poelmans (Ghent University)
"""
import os
import platform
import re
from vsc import fancylogger

from easybuild.tools.filetools import read_file, run_cmd


_log = fancylogger.getLogger('systemtools', fname=False)

# constants
AMD = 'AMD'
ARM = 'ARM'
INTEL = 'Intel'

LINUX = 'Linux'
DARWIN = 'Darwin'

UNKNOWN = 'UNKNOWN'


class SystemToolsException(Exception):
    """raised when systemtools fails"""


def get_avail_core_count():
    """
    Returns the number of available CPUs. This differs from get_core_count() in that it keeps
    cpusets in mind. When not in a cpuset, it returns get_core_count().
    Linux only for the moment.
    """
    os_type = get_os_type()

    if os_type == LINUX:
        mypid = os.getpid()
        try:
            f = open("/proc/%s/status" % mypid,'r')
            for line in f:
                cpuset = re.match("^Cpus_allowed_list:\s*([0-9,-]+)",line)
                if cpuset is not None:
                    break
            f.close()
            if cpuset is not None:
                cpuset_list = cpuset.group(1).split(',')
                numofcpus = 0
                for cpus in cpuset_list:
                    cpu_range = re.match("(\d+)-(\d+)",cpus)
                    if cpu_range is not None:
                        numofcpus += int(cpu_range.group(2))-int(cpu_range.group(1))+1
                    else:
                        numofcpus += 1

                _log.info("In cpuset with %s CPUs" % numofcpus)
                return numofcpus
        except IOError, err:
            _log.warning("Failed to read /proc/%s/status to determine the cpuset: %s" % (mypid, err))

    return get_core_count()


def get_core_count():
    """Try to detect the number of virtual or physical CPUs on this system.

    inspired by http://stackoverflow.com/questions/1006289/how-to-find-out-the-number-of-cpus-in-python/1006301#1006301
    """
    # Python 2.6+
    try:
        from multiprocessing import cpu_count
        return cpu_count()
    except (ImportError, NotImplementedError):
        pass

    # POSIX
    try:
        cores = int(os.sysconf('SC_NPROCESSORS_ONLN'))
        if cores > 0:
            return cores
    except (AttributeError, ValueError):
        pass

    os_type = get_os_type()

    if os_type == LINUX:
        try:
            txt = read_file('/proc/cpuinfo', log_error=False)
            # sometimes this is uppercase
            res = txt.lower().count('processor\t:')
            if res > 0:
                return res
        except IOError, err:
            raise SystemToolsException("An error occured while determining core count: %s" % err)
    else:
        # BSD
        try:
            out, _ = run_cmd('sysctl -n hw.ncpu')
            cores = int(out)
            if cores > 0:
                return cores
        except ValueError:
            pass

    raise SystemToolsException('Can not determine number of cores on this system')


def get_cpu_vendor():
    """Try to detect the cpu identifier

    will return INTEL, ARM or AMD constant
    """
    regexp = re.compile(r"^vendor_id\s+:\s*(?P<vendorid>\S+)\s*$", re.M)
    VENDORS = {
        'GenuineIntel': INTEL,
        'AuthenticAMD': AMD,
    }
    os_type = get_os_type()

    if os_type == LINUX:
        try:
            txt = read_file('/proc/cpuinfo', log_error=False)
            arch = UNKNOWN
            # vendor_id might not be in the /proc/cpuinfo, so this might fail
            res = regexp.search(txt)
            if res:
                arch = res.groupdict().get('vendorid', UNKNOWN)
            if arch in VENDORS:
                return VENDORS[arch]

            # some embeded linux on arm behaves differently (e.g. raspbian)
            regexp = re.compile(r"^Processor\s+:\s*(?P<vendorid>ARM\S+)\s*", re.M)
            res = regexp.search(txt)
            if res:
                arch = res.groupdict().get('vendorid', UNKNOWN)
            if ARM in arch:
                return ARM
        except IOError, err:
            raise SystemToolsException("An error occured while determining CPU vendor since: %s" % err)

    elif os_type == DARWIN:
        out, exitcode = run_cmd("sysctl -n machdep.cpu.vendor")
        out = out.strip()
        if not exitcode and out and out in VENDORS:
            return VENDORS[out]

    else:
        # BSD
        out, exitcode = run_cmd("sysctl -n hw.model")
        out = out.strip()
        if not exitcode and out:
            return out.split(' ')[0]

    return UNKNOWN


def get_cpu_model():
    """
    returns cpu model
    f.ex Intel(R) Core(TM) i5-2540M CPU @ 2.60GHz
    """
    os_type = get_os_type()
    if os_type == LINUX:
        regexp = re.compile(r"^model name\s+:\s*(?P<modelname>.+)\s*$", re.M)
        try:
            txt = read_file('/proc/cpuinfo', log_error=False)
            if txt is not None:
                return regexp.search(txt).groupdict()['modelname'].strip()
        except IOError, err:
            raise SystemToolsException("An error occured when determining CPU model: %s" % err)

    elif os_type == DARWIN:
        out, exitcode = run_cmd("sysctl -n machdep.cpu.brand_string")
        out = out.strip()
        if not exitcode:
            return out

    return UNKNOWN


def get_cpu_speed():
    """
    Returns the (maximum) cpu speed in MHz, as a float value.
    In case of throttling, the highest cpu speed is returns.
    """
    os_type = get_os_type()
    if os_type == LINUX:
        try:
             # Linux with cpu scaling
            max_freq_fp = '/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq'
            try:
                f = open(max_freq_fp, 'r')
                cpu_freq = float(f.read())/1000
                f.close()
                return cpu_freq
            except IOError, err:
                _log.warning("Failed to read %s to determine max. CPU clock frequency with CPU scaling: %s" % (max_freq_fp, err))

            # Linux without cpu scaling
            cpuinfo_fp = '/proc/cpuinfo'
            try:
                f = open(cpuinfo_fp, 'r')
                for line in f:
                    cpu_freq = re.match("^cpu MHz\s*:\s*([0-9.]+)", line)
                    if cpu_freq is not None:
                        break
                f.close()
                if cpu_freq is None:
                    raise SystemToolsException("Failed to determine CPU frequency from %s" % cpuinfo_fp)
                else:
                    return float(cpu_freq.group(1))
            except IOError, err:
                _log.warning("Failed to read %s to determine CPU clock frequency: %s" % (cpuinfo_fp, err))

        except (IOError, OSError), err:
            raise SystemToolsException("Determining CPU speed failed, exception occured: %s" % err)

    elif os_type == DARWIN:
        # OS X
        out, ec = run_cmd("sysctl -n hw.cpufrequency_max")
        # returns clock frequency in cycles/sec, but we want MHz
        mhz = float(out.strip())/(1000**2)
        if ec == 0:
            return mhz

    raise SystemToolsException("Could not determine CPU clock frequency (OS: %s)." % os_type)


def get_kernel_name():
    """Try to determine kernel name

    e.g., 'Linux', 'Darwin', ...
    """
    _log.deprecated("get_kernel_name() (replaced by os_type())", "2.0")
    try:
        kernel_name = os.uname()[0]
        return kernel_name
    except OSError, err:
        raise SystemToolsException("Failed to determine kernel name: %s" % err)


def get_os_type():
    """Determine system type, e.g., 'Linux', 'Darwin', 'Java'."""
    os_type = platform.system()
    if len(os_type) > 0:
        return os_type
    else:
        raise SystemToolsException("Failed to determine system name using platform.system().")


def get_shared_lib_ext():
    """Determine extention for shared libraries

    Linux: 'so', Darwin: 'dylib'
    """
    shared_lib_exts = {
        LINUX: 'so',
        DARWIN: 'dylib'
    }

    os_type = get_os_type()
    if os_type in shared_lib_exts.keys():
        return shared_lib_exts[os_type]
    else:
        raise SystemToolsException("Unable to determine extention for shared libraries,"
                                   "unknown system name: %s" % os_type)


def get_platform_name(withversion=False):
    """Try and determine platform name
    e.g., x86_64-unknown-linux, x86_64-apple-darwin
    """
    os_type = get_os_type()
    release = platform.release()
    machine = platform.machine()

    if os_type == LINUX:
        vendor = 'unknown'
        release = '-gnu'
    elif os_type == DARWIN:
        vendor = 'apple'
    else:
        raise SystemToolsException("Failed to determine platform name, unknown system name: %s" % os_type)

    platform_name = '%s-%s-%s' % (machine, vendor, os_type.lower())
    if withversion:
        platform_name += release

    return platform_name


def get_os_name():
    """
    Determine system name, e.g., 'redhat' (generic), 'centos', 'debian', 'fedora', 'suse', 'ubuntu',
    'red hat enterprise linux server', 'SL' (Scientific Linux), 'opensuse', ...
    """
    try:
        # platform.linux_distribution is more useful, but only available since Python 2.6
        # this allows to differentiate between Fedora, CentOS, RHEL and Scientific Linux (Rocks is just CentOS)
        os_name = platform.linux_distribution()[0].strip().lower()
    except AttributeError:
        # platform.dist can be used as a fallback
        # CentOS, RHEL, Rocks and Scientific Linux may all appear as 'redhat' (especially if Python version is pre v2.6)
        os_name = platform.dist()[0].strip().lower()
        _log.deprecated("platform.dist as fallback for platform.linux_distribution", "2.0")

    os_name_map = {
        'red hat enterprise linux server': 'RHEL',
        'scientific linux sl': 'SL',
        'scientific linux': 'SL',
        'suse linux enterprise server': 'SLES',
    }

    if os_name:
        return os_name_map.get(os_name, os_name)
    else:
        return UNKNOWN


def get_os_version():
    """Determine system version."""
    os_version = platform.dist()[1]
    if os_version:
        if get_os_name() in ["suse", "SLES"]:

            # SLES subversions can only be told apart based on kernel version,
            # see http://wiki.novell.com/index.php/Kernel_versions
            version_suffixes = {
                "11": [
                    ('2.6.27', ''),
                    ('2.6.32', '_SP1'),
                    ('3.0', '_SP2'),
                ],
            }

            # append suitable suffix to system version
            if os_version in version_suffixes.keys():
                kernel_version = platform.uname()[2]
                known_sp = False
                for (kver, suff) in version_suffixes[os_version]:
                    if kernel_version.startswith(kver):
                        os_version += suff
                        known_sp = True
                        break
                if not known_sp:
                    suff = '_UNKNOWN_SP'
            else:
                _log.error("Don't know how to determine subversions for SLES %s" % os_version)

        return os_version
    else:
        return UNKNOWN
