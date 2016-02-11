'''
.. codeauthor:: Richard Plevin

.. Copyright (c) 2016 Richard Plevin
   See the https://opensource.org/licenses/MIT for license details.
'''
import os
import io
import platform
from ConfigParser import SafeConfigParser
from .error import ConfigFileError

DEFAULT_SECTION = 'DEFAULT'
GCAM_SECTION    = 'GCAM'
USR_CONFIG_FILE = '.pygcam.cfg'
CONFIG_VAR_NAME = 'QUEUE_GCAM_CONFIG_FILE'
WORKSPACE_VAR_NAME   = 'QUEUE_GCAM_WORKSPACE'
NO_RUN_GCAM_VAR_NAME = 'QUEUE_GCAM_NO_RUN_GCAM'

# Stub ~/.gcam.cfg file which user can edit
_UserDefaults = \
"""
[GCAM]
# Add customizations here, e.g., set GCAM.Root to another directory
"""

# Deprecated
# # The default to which batchQuery.py writes CSV files.
# GCAM.OutputDir = %(GCAM.Workspace)s/output
#
# # Name of batch files generated by batchQuery. There should
# # be no need to change this, but you can if desired.
# GCAM.BatchFile = generated_batch_file.xml


_SystemDefaults = \
"""
[GCAM]
# Sets the folder holding the symlink "current" which refers
# to a folder holding Main_User_Workspace and ModelInterface.
# (This is one way of setting up the code, but not required.)
GCAM.Root = %(Home)s/GCAM

# Refers to the GCAM folder holding the version of the model
# you want to use. It is convenient to make this a symbolic link.
GCAM.Current = %(GCAM.Root)s/current

# The location of the Main_User_Workspace to use. This can refer
# to any folder; GCAM.Current is just an optional convention.
GCAM.Workspace = %(GCAM.Current)s/Main_User_Workspace

# The location of the ModelInterface to use.
GCAM.ModelInterface = %(GCAM.Current)s/ModelInterface

# The location of GCAM source code (for the purpose of reading
# the .csv file that defines the current regional aggregation.
GCAM.SourceWorkspace =

# The location of the default input file for runProject.py
GCAM.ProjectXmlFile = %(Home)/gcam_project.xml

# The location of the libraries needed by ModelInterface.
# (Not needed if using GCAM with BaseX rather than dbxml.)
GCAM.JavaLibPath = %(GCAM.Workspace)s/libs/dbxml/lib

# Arguments to java to ensure that ModelInterface has enough
# heap space.
GCAM.JavaArgs = -Xms512m -Xmx2g

# A string with one or more colon-delimited elements that identify
# directories or XML files in which to find batch query definitions.
GCAM.QueryPath = .

# The name of the queue used for submitting batch jobs on a cluster.
GCAM.DefaultQueue = standard

GCAM.QsubCommand = qsub -q {queueName} -N {jobName} -l walltime={walltime} -d {exeDir} -e {logFile} -m n -j oe -l pvmem=6GB -v QUEUE_GCAM_CONFIG_FILE='{configs}',QUEUE_GCAM_WORKSPACE='{workspace}',QUEUE_GCAM_NO_RUN_GCAM={noRunGCAM}

# --signal=USR1@15 => send SIGUSR1 15s before walltime expires
GCAM.SlurmCommand = sbatch -p {queueName} --nodes=1 -J {jobName} -t {walltime} -D {exeDir} --get-user-env=L -s --mem=6000 --tmp=6000 --export=QUEUE_GCAM_CONFIG_FILE='{configs}',QUEUE_GCAM_WORKSPACE='{workspace}',QUEUE_GCAM_NO_RUN_GCAM={noRunGCAM}

GCAM.BatchCommand = %(GCAM.QsubCommand)s

# Arguments to qsub's "-l" flag that define required resources
GCAM.QsubResources = pvmem=6GB

# Environment variables to pass to qsub. (Not needed by most users.)
GCAM.QsubEnviroVars =

# Default location in which to look for scenario directories
GCAM.ScenariosDir = %(GCAM.Root)s/scenarios

# For qsub, the default number of minutes to allocate per task.
GCAM.Minutes = 20

# Whether to use the "virtual buffer", allowing ModelInterface to
# run without generating pop-up windows on Linux.
GCAM.UseVirtualBuffer = yes

# A script to run by queueGCAM after GCAM completes. The script is
# called with 3 arguments: workspace directory, XML configuration
# file, and scenario name.
GCAM.PostProcessor =

# A file that maps GCAM regions to rename them or to aggregate
# them. Each line consists of a GCAM region name, some number of
# tabs, and the name to map the region to.
GCAM.RegionMapFile =

# Where to create temporary files
GCAM.TempDir = /tmp
"""

_ConfigParser = None

def getConfig():
    """
    Return the configuration object. If one has been created already via
    `readConfigFiles`, it is returned; otherwise a new one is created and
    the configuration files are read.

    :return: a `SafeConfigParser` instance.
    """
    if _ConfigParser:
        return _ConfigParser

    return readConfigFiles()

def readConfigFiles():
    """
    Read the pygcam configuration file, ``~/.pygcam.cfg``. "Sensible" default values are
    established first, which overwritten by values found in the user's configuration
    file.

    :return: a populated SafeConfigParser instance
    """
    global _ConfigParser

    home = os.getenv('HOME')
    platformName = platform.system()

    assert platformName in ('Darwin', 'Linux'), "Only Darwin (OS X) and Linux are supported currently"

    if platformName == 'Darwin':
        jarFile = '%(GCAM.ModelInterface)s/ModelInterface.app/Contents/Resources/Java/ModelInterface.jar'
        exeFile = 'Release/objects'
        useXvfb = 'False'
    elif platformName == 'Linux':
        jarFile = '%(GCAM.ModelInterface)s/ModelInterface.jar'
        exeFile = './gcam.exe'
        useXvfb = 'True'

    # Initialize config parser with default values
    _ConfigParser = SafeConfigParser()
    _ConfigParser.readfp(io.BytesIO(_SystemDefaults))
    _ConfigParser.set(GCAM_SECTION, 'Home', home)
    _ConfigParser.set(GCAM_SECTION, 'GCAM.Executable', exeFile)
    _ConfigParser.set(GCAM_SECTION, 'GCAM.JarFile', jarFile)
    _ConfigParser.set(GCAM_SECTION, 'GCAM.UseVirtualBuffer', useXvfb)

    # Customizations are stored in ~/.pygcam.cfg
    usrConfigPath = os.path.join(home, USR_CONFIG_FILE)
    if os.path.exists(usrConfigPath):
        _ConfigParser.readfp(open(usrConfigPath))
    else:
        # create an empty file with the [GCAM] section if no file exists
        with open(usrConfigPath, 'w') as fp:
            fp.write("[%s]\n" % GCAM_SECTION)
            fp.write(_UserDefaults)

    return _ConfigParser

def getParam(name):
    """
    Get the value of the configuration parameter `name`. Calls
    :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters. Note
       that variable names are case-insensitive.
    :return: (str) the value of the variable
    """
    if not _ConfigParser:
        getConfig()

    return _ConfigParser.get(GCAM_SECTION, name)

def getParamAsBoolean(name):
    """
    Get the value of the configuration parameter `name`, coerced
    into a boolean value, where any (case-insensitive) value in the
    set ``{'true','yes','on','1'}`` are converted to ``True``, and
    any value in the set ``{'false','no','off','0'}`` is converted to
    ``False``. Any other value raises an exception.
    Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :return: (bool) the value of the variable
    :raises: :py:exc:`pygcam.error.ConfigFileError`
    """
    true = ('true', 'yes', 'on', '1')
    false = ('false', 'no', 'off', '0')

    value = getParam(name)
    value = str(value).lower()

    if value in true:
        return True

    if value in false:
        return False

    raise ConfigFileError("The value of variable '%s' could not converted to boolean." % name)


def getParamAsInt(name):
    """
    Get the value of the configuration parameter `name`, coerced
    to an integer. Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :return: (int) the value of the variable
    """
    value = getParam(name)
    return int(value)

def getParamAsFloat(name):
    """
    Get the value of the configuration parameter `name` as a
    float. Calls :py:func:`getConfig` if needed.

    :param name: (str) the name of a configuration parameters.
    :return: (float) the value of the variable
    """
    value = getParam(name)
    return float(value)

