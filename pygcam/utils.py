'''
.. Created on: 2/12/15
   Common functions and data

.. Copyright (c) 2015-2016 Richard Plevin
   See the https://opensource.org/licenses/MIT for license details.
'''
import io
import os
from lxml import etree as ET
import pkgutil
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from tempfile import mkstemp, mkdtemp

from .config import getParam, getParamAsBoolean
from .error import PygcamException, FileFormatError
from .log import getLogger
from .windows import IsWindows

_logger = getLogger(__name__)

def queueForStream(stream):
    """
    Create a thread to read from a non-socket file descriptor and
    its contents to a socket so non-blocking read via select() works
    on Windows. (Since Windows doesn't support select on pipes.)

    :param stream: (file object) the input to read from,
       presumably a pipe from a subprocess
    :return: (int) a file descriptor for the socket to read from.
    """
    from six.moves.queue import Queue
    from threading import Thread

    def enqueue(stream, queue):
        fd = stream.fileno()
        data = None
        while data != b'':
            data = os.read(fd, 1024)
            queue.put(data)
        stream.close()

    q = Queue()
    t = Thread(target=enqueue, args=(stream, q))
    t.daemon = True # thread dies with the program
    t.start()

    return q

# Used only in CI plugins
def digitColumns(df, asInt=False):
    '''
    Get a list of columns with integer names (as strings, e.g., "2007") in df.
    If asInt is True return as a list of integers, otherwise as strings.
    '''
    digitCols = filter(str.isdigit, df.columns)
    return map(int, digitCols) if asInt else digitCols

# Function to return current function name, or the caller, and so on up
# the stack, based on value of n.
getFuncName = lambda n=0: sys._getframe(n + 1).f_code.co_name

pat = re.compile('(\{[^\}]+\})')

# used only in project.py
def simpleFormat(s, varDict):
    """
    Simple version of str.format that does not treat '.' as
    an attribute reference.

    :param s: (str) string with args in curly braces
    :param varDict: (dict) dictionary of var names and values
    :return: (str) formatted string
    """
    def lookup(m):
        match = m.group(0)
        key = match[1:-1]   # strip off curly braces
        return str(varDict[key])

    try:
        result = re.sub(pat, lookup, s)
        return result
    except KeyError as e:
        raise FileFormatError('Unknown parameter %s in project XML template string' % e)

@contextmanager
def pushd(directory):
    """
    Context manager that changes to the given directory and then
    returns to the original directory. Usage is ``with pushd('/foo/bar'): ...``

    :param directory: (str) a directory to chdir to temporarily
    :return: none
    """
    owd = os.getcwd()
    try:
        os.chdir(directory)
        yield directory
    finally:
        os.chdir(owd)

def getResource(relpath):
    """
    Extract a resource (e.g., file) from the given relative path in
    the pygcam package.

    :param relpath: (str) a path relative to the pygcam package
    :return: the file contents
    """
    contents = pkgutil.get_data('pygcam', relpath)
    return contents

def resourceStream(relpath):
    """
    Return a stream on the resource found on the given path relative
    to the pygcam package.

    :param relpath: (str) a path relative to the pygcam package
    :return: (file-like stream) a file-like buffer opened on the desired resource.
    """
    text = getResource(relpath)
    return io.BytesIO(text)

def copyResource(relpath, dest, overwrite=True):
    """
    Copy a resource from the 'pygcam' package to the given destination.

    :param relpath: (str) a path relative to the pygcam package
    :param dest: (str) the pathname of the file to create by copying the resource.
    :param overwrite: (bool) if False, raise an error if the destination
      file already exists.
    :return: none
    """
    if not overwrite and os.path.lexists(dest):
        raise FileFormatError(dest)

    text = getResource(relpath)
    with open(dest, 'w') as f:
        f.write(text)

# used only in gcamtool modules
def getBooleanXML(value):
    """
    Get a value from an XML file and convert it into a boolean True or False.

    :param value: any value (it's first converted to a string)
    :return: True if the value is in ['true', 'yes', '1'], False if the value
             is in ['false', 'no', '0']. An exception is raised if any other
             value is passed.
    :raises: PygcamException
    """
    false = ["false", "no", "0"]
    true  = ["true", "yes", "1"]

    val = str(value).strip()
    if val not in true + false:
        raise PygcamException("Can't convert '%s' to boolean; must be in {false,no,0,true,yes,1} (case sensitive)." % value)

    return (val in true)


_XMLDBPropertiesTemplate = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">
<!-- WARNING: this file is automatically generated. Manual edits will be overwritten. -->
<properties>
	<entry key="in-memory">{inMemory}</entry>
	<entry key="filter-script">{filterFile}</entry>
	<entry key="batch-logfile">{batchLog}</entry>
	<entry key="batch-queries">{batchFile}</entry>
</properties>
"""

def writeXmldbDriverProperties(outputDir='.', inMemory=True, filterFile='', batchFile='', batchLog=''):
    """
    Write a XMLDBDriver.properties file using the values passed in the arguments.

    :param outputDir: (str) where to write the file
    :param inMemory: (bool) if True, the ``in-memory`` attribute is set to True
    :param filterFile: (str) a file that filters GCAM query output to limit what's
       written to the database
    :param batchFile: (str) the path to an XML batch query file
    :param batchLog: (str) the path to a log file into which to direct
       batch query messages (queries can be pretty verbose...)
    :return: none
    """
    path = pathjoin(outputDir, 'XMLDBDriver.properties')
    memFlag = 'true' if inMemory else 'false'
    content = _XMLDBPropertiesTemplate.format(inMemory=memFlag, filterFile=filterFile,
                                              batchFile=batchFile, batchLog=batchLog)

    deleteFile(path) # avoid writing through symlinks to ref workspace
    with open(path, 'w') as f:
        f.write(content)

def deleteFile(filename):
    """
    Delete the given `filename`, but ignore errors, like "rm -f"

    :param filename: (str) the file to remove
    :return: none
    """
    try:
        os.remove(filename)
    except:
        pass    # ignore errors, like "rm -f"

# used only in gcamtool modules
def symlinkOrCopyFile(src, dst):
    """
    Symlink a file unless GCAM.CopyAllFiles is True, in which case, copy the file.

    :param src: (str) filename of original file
    :param dst: (dst) filename of copy
    :return: none
    """
    if getParamAsBoolean('GCAM.CopyAllFiles'):
        copyFileOrTree(src, dst)
    else:
        os.symlink(src, dst)

def copyFileOrTree(src, dst):
    """
    Copy src to dst, where the two can both be files or directories.
    If `src` and `dst` are directories, `dst` must not exist yet.

    :param src: (str) path to a source file or directory
    :param dst: (str) path to a destination file or directory.
    :return: none
    """
    if getParamAsBoolean('GCAM.CopyAllFiles') and src[0] == '.':   # convert relative paths
        src = unixPath(os.path.normpath(os.path.join(os.path.dirname(dst), src)))

    if os.path.islink(src):
        src = os.readlink(src)

    if os.path.isdir(src):
        removeTreeSafely(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

# used only in gcamtool modules
# TBD: rename to removeTree
def removeTreeSafely(path, ignore_errors=True):
    refWorkspace = os.path.realpath(getParam('GCAM.RefWorkspace'))
    thisPath = os.path.realpath(path)
    if os.path.commonprefix((refWorkspace, thisPath)) == refWorkspace:
        raise PygcamException("Refusing to delete %s, which is part of the reference workspace" % path)

    _logger.debug("shutil.rmtree('%s')", thisPath)
    shutil.rmtree(thisPath, ignore_errors=ignore_errors)

def removeFileOrTree(path, raiseError=True):
    """
    Remove a file or an entire directory tree. Handles removal of symlinks
    on Windows, as these are treated differently in that system.

    :param path: (str) the pathname of a file or directory.
    :param raiseError: (bool) if True, re-raise any error that occurs
       during the file operations, else errors are ignored.
    :return: none
    """
    from .windows import removeSymlink

    if not os.path.lexists(path):
        return

    try:
        if os.path.islink(path):
            # Windows treats links to files and dirs differently.
            # NB: if not on Windows, just calls os.remove()
            removeSymlink(path)
        else:
            if os.path.isdir(path):
                removeTreeSafely(path)
            else:
                os.remove(path)
    except Exception as e:
        if raiseError:
            raise

def systemOpenFile(path):
    """
    Ask the operating system to open a file at the given pathname.

    :param path: (str) the pathname of a file to open
    :return: none
    """
    import platform
    from subprocess import call

    if platform.system() == 'Windows':
        call(['start', os.path.abspath(path)], shell=True)
    else:
        # "-g" => don't bring app to the foreground
        call(['open', '-g', path], shell=False)

def coercible(value, type, raiseError=True):
    """
    Attempt to coerce a value to `type` and raise an error on failure.

    :param value: any value coercible to `type`
    :param type: any Python type
    :return: (`type`) the coerced value, if it's coercible, otherwise
       None if raiseError is False
    :raises PygcamException: if not coercible and raiseError is True
    """
    try:
        value = type(value)
    except (TypeError, ValueError) as e:
        if raiseError:
            raise PygcamException("%s: %r is not coercible to %s" % (getFuncName(1), value, type.__name__))
        else:
            return None

    return value

def unixPath(path, rmFinalSlash=False):
    """
    Convert a path to use Unix-style slashes, optionally
    removing the final slash, if present.

    :param path: (str) a pathname
    :param rmFinalSlash: (bool) True if a final slash should
           be removed, if present.
    :return: (str) the modified pathname
    """
    if IsWindows:
        path = path.replace('\\', '/')

    if rmFinalSlash and path[-1] == '/':
        path = path[0:-1]

    return path

def pathjoin(*elements, **kwargs):
    path = os.path.join(*elements)

    if kwargs.get('normpath'):
        path = os.path.normpath(path)

    if kwargs.get('realpath'):
        path = os.path.realpath(path)

    return unixPath(path, rmFinalSlash=True)

def shellCommand(command, shell=True, raiseError=True):
    """
    Run a shell command and optionally raise PygcamException error.

    :param command: the command to run, with arguments. This can be expressed
      either as a string or as a list of strings.
    :param shell: if True, run `command` in a shell, otherwise run it directly.
    :param raiseError: if True, raise `ToolError` on command failure.
    :return: exit status of executed command
    :raises: ToolError
    """
    _logger.info(command)
    exitStatus = subprocess.call(command, shell=shell)
    if exitStatus != 0:
        if raiseError:
            raise PygcamException("\n*** Command failed: %s\n*** Command exited with status %s\n" % (command, exitStatus))

    return exitStatus

def flatten(listOfLists):
    """
    Flatten one level of nesting given a list of lists. That is, convert
    [[1, 2, 3], [4, 5, 6]] to [1, 2, 3, 4, 5, 6].

    :param listOfLists: a list of lists, obviously
    :return: the flattened list
    """
    from itertools import chain

    return list(chain.from_iterable(listOfLists))

def ensureExtension(filename, ext):
    """
    Force a filename to have the given extension, `ext`, adding it to
    any other extension, if present. That is, if `filename` is ``foo.bar``,
    and `ext` is ``baz``, the result will be ``foo.bar.baz``.
    If `ext` doesn't start with a ".", one is added.

    :param filename: filename
    :param ext: the desired filename extension
    :return: filename with extension `ext`
    """
    mainPart, extension = os.path.splitext(filename)
    ext = ext if ext[0] == '.' else '.' + ext

    if not extension:
        filename = mainPart + ext
    elif extension != ext:
        filename += ext

    return filename

def ensureCSV(file):
    """
    Ensure that the file has a '.csv' extension by replacing or adding
    the extension, as required.

    :param file: (str) a filename
    :return: (str) the filename with a '.csv' extension.
    """
    return ensureExtension(file, '.csv')

def getYearCols(years, timestep=5):
    """
    Generate a list of names of year columns in GCAM result files from a
    string indicating a year range.

    :param years: (str) a string of the form "2020-2050"
    :param timestep: (int) the number of years between timesteps
    :return: (list of strings) the names of the corresponding columns
    """
    try:
        yearRange = map(int, years.split('-'))
        if not len(yearRange) == 2:
            raise Exception
    except:
        raise Exception('Years must be specified as two years separated by a hyphen, as in "2020-2050"')

    cols = map(str, range(yearRange[0], yearRange[1]+1, timestep))
    return cols

def saveToFile(txt, dirname='', filename=''):
    """
    Save the given text to a file in the given directory.

    :param txt: (str) the text to save
    :param dirname: (str) path to a directory
    :param filename: (str) the name of the file to create

    :return: none
    """
    if dirname:
        mkdirs(dirname)

    pathname = pathjoin(dirname, filename)

    _logger.debug("Writing %s", pathname)
    with open(pathname, 'w') as f:
        f.write(txt)

QueryResultsDir = 'queryResults'

def getBatchDir(scenario, resultsDir):
    """
    Get the name of the directory holding batch query results..

    :param scenario: (str) the name of a scenario
    :param resultsDir: (str) the directory in which the batch
        results directory should be created
    :return: (str) the pathname to the batch results directory
    """
    pathname = pathjoin(resultsDir, scenario, QueryResultsDir)
    return pathname


def mkdirs(newdir, mode=0o770):
    """
    Try to create the full path `newdir` and ignore the error if it already exists.

    :param newdir: the directory to create (along with any needed parent directories)
    :return: nothing
    """
    from errno import EEXIST

    try:
        os.makedirs(newdir, mode)
    except OSError as e:
        if e.errno != EEXIST:
            raise

def getExeDir(workspace, chdir=False):
    workspace = os.path.abspath(os.path.expanduser(workspace))     # handle ~ in pathname
    exeDir    = pathjoin(workspace, 'exe')

    if chdir:
        _logger.info("cd %s", exeDir)
        os.chdir(exeDir)

    return exeDir

def loadModuleFromPath(modulePath, raiseOnError=True):
    """
    Load a module from a '.py' or '.pyc' file from a path that ends in the
    module name, i.e., from "foo/bar/Baz.py", the module name is 'Baz'.

    :param modulePath: (str) the pathname of a python module (.py or .pyc)
    :param raiseOnError: (bool) if True, raise an error if the module cannot
       be loaded
    :return: (module) a reference to the loaded module, if loaded, else None.
    :raises: PygcamException
    """
    from imp import load_source, load_compiled  # lazy import to speed startup

    # Extract the module name from the module path
    modulePath = unixPath(modulePath)
    base       = os.path.basename(modulePath)
    moduleName = base.split('.')[0]

    _logger.debug('loading module %s' % modulePath)

    # Load the compiled code if it's a '.pyc', otherwise load the source code
    module = None
    try:
        module = load_source(moduleName, modulePath)
    except Exception as e:
        errorString = "Can't load module %s from path %s: %s" % (moduleName, modulePath, e)
        if raiseOnError:
            raise PygcamException(errorString)

        _logger.error(errorString)

    return module

# Deprecated (unused, but might still be useful)
# def loadObjectFromPath(objName, modulePath, required=True):
#     """
#     Load a module and return a reference to a named object in that module.
#     If 'required' and the object is not found, an error is raised, otherwise,
#     None is returned if the object is not found.
#
#     :param objName: (str) the name of an object to find in the `modulePath`
#     :param modulePath: (str) the path to a python module to load
#     :param required: (bool) if True and the object cannot be loaded, raise
#       an error.
#     :return: a reference to the loaded object, if loaded. If not loaded and
#        `required` is False, return None.
#     :raises: PygcamException
#     """
#     module = loadModuleFromPath(modulePath)
#     obj    = getattr(module, objName, None)
#
#     if obj or not required:
#         return obj
#
#     raise PygcamException("Module '%s' has no object named '%s'" % (modulePath, objName))

def importFrom(modname, objname, asTuple=False):
    """
    Import `modname` and return reference to `objname` within the module.

    :param modname: (str) the name of a Python module
    :param objname: (str) the name of an object in module `modname`
    :param asTuple: (bool) if True a tuple is returned, otherwise just the object
    :return: (object or (module, object)) depending on `asTuple`
    """
    from importlib import import_module

    module = import_module(modname, package=None)
    obj    = getattr(module, objname)
    return module, obj if asTuple else obj

def importFromDotSpec(spec):
    """
    Import an object from an arbitrary dotted sequence of packages, e.g.,
    "a.b.c.x" by splitting this into "a.b.c" and "x" and calling importFrom().

    :param spec: (str) a specification of the form package.module.object
    :return: none
    :raises PygcamException: if the import fails
    """
    modname, objname = spec.rsplit('.', 1)

    try:
        return importFrom(modname, objname)

    except ImportError:
        raise PygcamException("Can't import '%s' from '%s'" % (objname, modname))

# Deprecated?
def readScenarioName(configFile):
    """
    Read the file `configFile` and extract the scenario name.

    :param configFile: (str) the path to a GCAM configuration file
    :return: (str) the name of the scenario defined in `configFile`
    """
    parser = ET.XMLParser(remove_blank_text=True)
    tree   = ET.parse(configFile, parser)
    scenarioName = tree.find('//Strings/Value[@name="scenarioName"]')
    return scenarioName.text

def printSeries(series, label, header='', asStr=False):
    """
    Print a `series` of values, with a give `label`.

    :param series: (convertible to pandas Series) the values
    :param label: (str) a label to print for the data
    :return: none
    """
    import pandas as pd

    if type(series) == pd.DataFrame:
        df = series
        df = df.T
    else:
        df = pd.DataFrame(pd.Series(series))  # DF is more convenient for printing

    df.columns = [label]

    oldPrecision = pd.get_option('precision')
    pd.set_option('precision', 5)
    s = "%s\n%s" % (header, df.T)
    pd.set_option('precision', oldPrecision)

    if asStr:
        return s
    else:
        print(s)

def getTempFile(suffix='', tmpDir=None, text=True, delete=True):
    """
    Convenience function for common use pattern, which is to get
    the name of a temp file that needs to be deleted on app exit.

    :param suffix: (str) an extension to give the temporary file
    :param tmpDir: (str) the directory in which to create the file.
      (Defaults to the value of configuration file variable 'GCAM.TempDir',
      or '/tmp' if the variable is not found.
    :param text: True if this will be a text file
    :param delete: (bool) if False, don't delete the file on cleanup.
       (This is useful for debugging.)
    :return: (str) pathname of a new temporary file
    """
    obj = TempFile(suffix=suffix, text=text, tmpDir=tmpDir, delete=delete)
    return obj.path

def getTempDir(suffix='', tmpDir=None, delete=True):
    """
    Convenience function for common use pattern, which is to get the
    name of a temporary directory that needs to be deleted on app exit.

    :param suffix: (str) an extension to give the temporary file
    :param tmpDir: (str) the directory in which to create the new temporary
        directory (Defaults to the value of configuration file variable
        'GCAM.TempDir', or '/tmp' if the variable is not found.
    :param delete: (bool) if False, don't delete the file on cleanup.
       (This is useful for debugging.)
    :return: (str) pathname of a new temporary directory
    """
    obj = TempFile(suffix=suffix, tmpDir=tmpDir, delete=delete, createDir=True)
    return obj.path


class TempFile(object):
    """
    Class to create and track temporary files in one place
    so they can be deleted before an application exits.
    """
    Instances = {}

    def __init__(self, path=None, suffix='', tmpDir=None, delete=True,
                 openFile=False, text=True, createDir=False):
        """
        Construct the name of a temporary file.

        :param path: (str) a path to register for deletion. If given, all other
            args are ignored.
        :param suffix: (str) an extension to give the temporary file
        :param tmpDir: (str) the directory in which to create the (defaults to
            the value of configuration file variable 'GCAM.TempDir', or '/tmp'
            if the variable is not found.
        :param delete: (bool) whether deleteFile() should delete the file when
            called
        :param openFile: (bool) whether to leave the new file open (ignored if
            createDir is True)
        :param text: (bool) Set to False if this will not be a text file
        :param createDir: (bool) if True, a temporary directory will be created
            rather than a temporary file.
        :return: none
        """
        self.suffix = suffix
        self.delete = delete
        self.fd = None

        if path:
            # If we're called with a path, it's just to register a file for deletion.
            # We ignore all other parameters.
            self.path = path
        else:
            tmpDir = tmpDir or getParam('GCAM.TempDir') or "/tmp"
            mkdirs(tmpDir)

            if createDir:
                self.path = mkdtemp(suffix=suffix, dir=tmpDir)
            else:
                fd, tmpFile = mkstemp(suffix=suffix, dir=tmpDir, text=text)

                self.path = tmpFile
                if openFile:
                    self.fd = fd
                else:
                    # the caller is just after a pathname, so close it here
                    os.close(fd)
                    os.unlink(tmpFile)

        # save this instance by the unique path
        self.Instances[self.path] = self

    def deleteFile(self):
        """
        Remove the file for a TempFile instance if ``self.delete`` is True. In either
        case, delete the instance from the class instance dict.

        :return: none
        :raises: PygcamException if the path is not related to a TempFile instance.
        """
        path = self.path

        try:
            if self.fd is not None:
                os.close(self.fd)
        except Exception as e:
            _logger.debug('Failed to close file descriptor for "%s": %s', path, e)

        try:
            del self.Instances[path]
        except KeyError:
            raise PygcamException('No TempFile instance with name "%s"' % path)

        deleting = 'Deleting' if self.delete else 'Not deleting'
        _logger.debug("%s TempFile file '%s'", deleting, path)

        if self.delete:
            try:
                removeFileOrTree(path, raiseError=True)
            except Exception as e:
                _logger.debug('Failed to delete "%s": %s', path, e)

    @classmethod
    def deleteAll(cls):
        for obj in cls.Instances.values():
            obj.deleteFile()

    @classmethod
    def remove(cls, filename, raiseError=True):
        """
        Remove a temporary file and delete the TempFile instance from the dict.

        :param filename: (str) the name of a temp file created by this class
        :param raiseError: (bool) if True, raise an exception if the filename is
            not a known TempFile.
        :return: none
        :raises PygcamException: if the path is not related to a TempFile instance.
        """
        try:
            obj = cls.Instances[filename]
            obj.deleteFile()
        except KeyError:
            if raiseError:
                raise PygcamException('No TempFile instance with name "%s"' % filename)


TRIAL_STRING_DELIMITER = ','

# Unused
def parseTrialString(string):
    """
    Converts a comma-separated list of ranges into a list of numbers.
    Ex. 1,3,4-6,2 becomes [1,3,4,5,6,2]. Duplicates are deleted. This
    function is the inverse of :func:`createTrialString`.

    :param string: (str) comma-separate list of ints or int ranges indicated
      by two ints separated by a hyphen.
    :return: (list) a list of ints
    """
    rangeStrs = string.split(TRIAL_STRING_DELIMITER)
    res = set()
    for rangeStr in rangeStrs:
        r = [int(x) for x in rangeStr.strip().split('-')]
        if len(r) == 2:
            r = range(r[0], r[1] + 1)
        elif len(r) != 1:
            raise ValueError('Malformed trial string.')
        res = res.union(set(r))
    return list(res)

# Unused
def createTrialString(lst):
    '''
    Assemble a list of numbers into a compact list using hyphens to identify ranges.
    This function is the inverse of :func:`parseTrialString`.
    '''
    from itertools import groupby   # lazy imports
    from operator import itemgetter

    lst = sorted(set(lst))
    ranges = []
    for _, g in groupby(enumerate(lst), lambda (i, x): i - x):
        group = map(lambda x: str(itemgetter(1)(x)), g)
        if group[0] == group[-1]:
            ranges.append(group[0])
        else:
            ranges.append(group[0] + '-' + group[-1])
    return TRIAL_STRING_DELIMITER.join(ranges)

# Unused
def chunkify(lst, chunks):
    """
    Iterator to turn a list into the number of lists given by chunks.
    In the case that len(lst) % chunksize != 0, all chunks are made as
    close to the same size as possible.

    :param lst: (list) a list of values
    :param chunks: (int) the number of chunks to create
    :return: iterator that returns one chunk at a time
    """
    l = len(lst)
    numWithExtraEntry = l % chunks  # this many chunks have one extra entry
    chunkSize = (l - numWithExtraEntry) / chunks + 1
    switch = numWithExtraEntry * chunkSize

    i = 0
    while i < l:
        if i == switch:
            chunkSize -= 1
        yield lst[i:i + chunkSize]
        i += chunkSize
