#!/usr/bin/env python2
# vim: set fileencoding=utf-8 :
#
# MIT License
#
# Copyright (c) 2020 Zdzisław Śliwiński
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import heapq
import os
import re
import subprocess
import sys

from clang.cindex import Config
from clang.cindex import CursorKind
from clang.cindex import Diagnostic
from clang.cindex import Index
from clang.cindex import TranslationUnitLoadError

def printErrorsAndExit(errors):
    """
    Write `errors' to stderr and exit with error code == 1.
    """

    for err in errors:
        sys.stderr.write(err)
        sys.stderr.write("\n")
    sys.exit(1)

class Settings(object):
    """
    Provide namespace for script-wide settings.

    Also, initialise the settings to their default values.
    """

    libclangSo = "/usr/lib/llvm-3.8/lib/libclang.so.1"
    shouldSort = True
    cxxFlags = []
    defines = []
    userIncludes = []
    systemIncludes = []
    shouldCollectSystemIncludes = True
    shouldUseCtags = True
    inputTagfile = ""
    inputFilenames = []
    outputTagfile = ""
    outputSyntaxfile = ""
    currentFilename = ""

    @staticmethod
    def parseArgv(argv):
        """
        Initialise settings with values from `argv'.
        """

        parser = argparse.ArgumentParser(
            description="Generate tagfile and syntax file for C++ source code."
        )
        parser.add_argument(
            "--libclang",
            metavar="<filename>",
            default=Settings.libclangSo,
            help="Full pathname to libclang.so file (default: '%s')." % (
                Settings.libclangSo
            )
        )
        parser.add_argument(
            "-S", "--no-sort",
            action="store_true",
            default=not Settings.shouldSort,
            help="Don't sort the output tagfile."
        )
        parser.add_argument(
            "-c",
            dest="cxx_flags",
            action="append",
            metavar="<compilation flag>",
            default=Settings.cxxFlags,
            help="Clang compilation flag (e.g.: std=c++14, fPIC). Can be specified multiple times."
        )
        parser.add_argument(
            "-d",
            dest="defines",
            action="append",
            metavar="<define>",
            default=Settings.defines,
            help="Define that is passed to clang as the -D option (e.g. TRACE, MT_FLAG=1). Can be specified multiple times."
        )
        parser.add_argument(
            "-I",
            dest="user_includes",
            action="append",
            metavar="<include dir>",
            default=Settings.userIncludes,
            help="User include directory that is passed to clang as the -I option. Can be specified multiple times."
        )
        parser.add_argument(
            "-i",
            dest="system_includes",
            action="append",
            metavar="<include dir>",
            default=Settings.systemIncludes,
            help="System include directory that is passed to clang as the -I option. Can be specified multiple times."
        )
        parser.add_argument(
            "-Y", "--no-collect-system-includes",
            action="store_true",
            default=not Settings.shouldCollectSystemIncludes,
            help="Don't collect tags from system includes. For the option to be in effect, the system include directory has to be specified with the '-i' option."
        )
        parser.add_argument(
            "-C", "--no-use-ctags",
            action="store_true",
            default=not Settings.shouldUseCtags,
            help="Don't use ctags to collect macro definitions."
        )
        parser.add_argument(
            "-t", "--tagfile",
            metavar="<filename>",
            default=Settings.inputTagfile,
            help="Input tagfile. The purpose of this option is to allow incremental updates of tagfiles. When this option is specified collecting of tags is limited to the input C++ source files exclusively, i.e. the logic for collecting tags from files that are directly or indirectly #included is disabled."
        )
        parser.add_argument(
            "filenames",
            metavar="<input file>",
            nargs="+",
            default=[],
            help="Input C++ source file."
        )
        parser.add_argument(
            "-o",
            dest="output_tagfile",
            metavar="<filename>",
            default=None,
            help="Output tagfile. No output is produced if this option is not specified. If the <filename> is '-' the output tagfile is sent to stdout."
        )
        parser.add_argument(
            "-s",
            dest="output_syntaxfile",
            metavar="<filename>",
            default=None,
            help="Output syntax file. No output is produced if this option is not specified. If the <filename> is '-' the output syntax file is sent to stdout."
        )
        args = parser.parse_args(argv[1:])

        Settings.libclangSo = args.libclang
        Settings.shouldSort = not args.no_sort
        Settings.cxxFlags = args.cxx_flags
        Settings.defines = args.defines
        Settings.userIncludes = args.user_includes
        Settings.systemIncludes = args.system_includes
        Settings.shouldCollectSystemIncludes = not args.no_collect_system_includes
        Settings.shouldUseCtags = not args.no_use_ctags
        Settings.inputTagfile = args.tagfile
        Settings.inputFilenames = args.filenames
        Settings.outputTagfile = args.output_tagfile
        Settings.outputSyntaxfile = args.output_syntaxfile

class Collector(object):
    """
    Class for collecting and writing out the collected tags.

    To avoid duplicates in the output tagfile the following strategy is
    deployed:
    - when tags are sorted (`Settings.shouldSort' == True), collect all tags
      from the coming cursors and only when outputting the tags, make sure that
      duplicates are avoided;
    - when tags are *not* sorted (`Settings.shouldSort' == False), collect tags
      only if they were not previously collected.
    """

    fieldsDefs = {}
    fields = {}
    reTagEntry = re.compile(r'^([^\t]+)\t([^\t]+)\t([^\t]+);"\t(.*)$')
    reDefine = re.compile(r'^(\w+).*$')
    reFunctionTemplate = re.compile('^(.*)<[^>]*>$')

    def __init__(self):
        self.tags = []
        self.types = set()
        self.constants = set()
        self.functions = set()
        self.identifiers = set()
        self.syntaxGroups = [
            [ "Identifier", self.identifiers ], # lowest priority first
            [ "Function", self.functions ],
            [ "Constant", self.constants ],
            [ "cppUserType", self.types ]
        ]

    @staticmethod
    def canCollect(child):
        """
        Determine whether the cursor `child' is collectable.

        Return:
        True -- `child' can be used for collecting a tag
        False -- otherwise
        """

        if not hasattr(child.location.file, 'name'):
            return False
        return (
            (len(child.spelling) > 0) and
            (child.kind in Collector.fields) and
            (
                next(
                    (
                        si for si in Settings.systemIncludes
                            if child.location.file.name.startswith(si)
                    ),
                    None
                ) is None
                if not Settings.shouldCollectSystemIncludes else True
            ) and
            (
                child.is_definition()
                if child.kind in Collector.fieldsDefs else True
            ) and
            (
                child.location.file.name.endswith(Settings.currentFilename)
                if Settings.inputTagfile != "" else True
            )
        )

    def collectTags(self, tu):
        """
        Collect tags from the TranslationUnit `tu'.

        Also, when `Settings.shouldUseCtags' == True, collect tags that are
        macro definitions.
        """

        self.collectChildTags(tu.cursor.get_children())
        if Settings.shouldUseCtags:
            self.collectMacroTags()

    def collectChildTags(self, children):
        """
        Collect tags from each cursor in `children'.
        """

        for c in children:
            self.collectChildTag(c)

    def collectChildTag(self, child):
        """
        Collect a tag from `child'.

        Also, collect a tag for the file that is indicated by `child'.
        """

        if Collector.canCollect(child):
            name = Collector.fields[child.kind][1](self, child.spelling)
            filename = child.location.file.name

            tag = (os.path.basename(filename), filename)
            self.addTag(tag)

            tag = (
                name,
                filename,
                child.location.line,
                child.location.column,
                child.kind
            )
            self.addTag(tag)

        self.collectChildTags(child.get_children())

    def collectMacroTags(self):
        """
        Use `ctags' to collect tags that are macro definitions.
        """

        filenames = []
        for tag in self.tags:
            if len(tag) == 2: # file tag
                filename = tag[1]
                if not filename in filenames:
                    filenames.append(filename)

        if len(filenames) > 0:
            args = [
                "ctags",
                "--c++-kinds=d",
                "--sort=%s" % ("yes" if Settings.shouldSort else "no"),
                "-o",
                "-",
            ]
            args.extend(filenames)

            sp = subprocess.Popen(args, stdout=subprocess.PIPE)
            (out, _) = sp.communicate()

            for ln in out.split("\n"):
                mo = Collector.reTagEntry.search(ln)
                if not mo is None:
                    tag = mo.group(1, 2, 3)
                    self.addTag(tag)
                    self.addConstant(tag[0])

            for d in Settings.defines:
                mo = Collector.reDefine.search(d)
                if not mo is None:
                    name = mo.group(1)
                    tag = ( name, "<command-line>", "0" )
                    self.addTag(tag)
                    self.addConstant(tag[0])

    def addTag(self, tag):
        """
        Add `tag' to `self.tags'.

        Depending on the value of `Settings.shouldSort', `tag' is added to
        either a heapq (`Settings.shouldSort' == True) or a list
        (`Settings.shouldSort' == False).
        """

        if Settings.shouldSort:
            heapq.heappush(self.tags, tag)
        else:
            if not tag in self.tags:
                self.tags.append(tag)

    @staticmethod
    def isAllowedName(name):
        """
        Filter out names that are options to Vim's syntax commands.
        """

        return not name in [
            "cchar",
            "conceal",
            "concealends",
            "contained",
            "containedin",
            "contains",
            "display",
            "extend",
            "fold",
            "nextgroup",
            "oneline",
            "skipempty",
            "skipnl",
            "skipwhite",
            "transparent"
        ]

    def addType(self, name):
        """
        Add `name' to `self.types'.
        """

        if Collector.isAllowedName(name):
            self.types.add(name)
        return name

    def addConstant(self, name):
        """
        Add `name' to `self.constants'.
        """

        if Collector.isAllowedName(name):
            self.constants.add(name)
        return name

    def addFunction(self, name):
        """
        Add `name' to `self.functions'.

        `name' is not added to `self.functions' if it is operator function.
        If `name' is of form 'fn<T>' (i.e. it is function template) the template
        arguments are removed before adding, i.e. 'fn<T> becomes 'fn'.
        """

        if Collector.isAllowedName(name) and not name.startswith("operator"):
            name = Collector.reFunctionTemplate.sub(r"\1", name)
            self.functions.add(name)
        return name

    def addIdentifier(self, name):
        """
        Add `name' to `self.identifiers'.
        """

        if Collector.isAllowedName(name):
            self.identifiers.add(name)
        return name

    def writeTags(self, writer):
        """
        Write out the collected tags on the provided `writer'.

        Also, make sure that there are no duplicate tags in the output when the
        tags are sorted. (Note: when the tags are *not* sorted, there should be
        no duplicates already).
        """

        lastTag = None
        for i in range(len(self.tags)):
            if Settings.shouldSort:
                tag = heapq.heappop(self.tags)
                if tag != lastTag:
                    lastTag = tag
                else:
                    tag = None
            else:
                tag = self.tags[i]

            if not tag is None:
                if len(tag) == 2: # file tag
                    writer.writeLine(
                        '%s\t%s\t1;"\tkind:F' % (
                            tag[0], # basename
                            tag[1] # filename
                        )
                    )
                elif len(tag) == 3: # macro definition tag
                    writer.writeLine(
                        '%s\t%s\t%s;"\tkind:d' % (
                            tag[0], # macro name
                            tag[1], # filename
                            tag[2] # ex command (e.g. line number)
                        )
                    )
                elif len(tag) == 4: # tag sourced from input tagfile
                    writer.writeLine(
                        '%s\t%s\t%s;"\t%s' % (
                            tag[0], # tagname
                            tag[1], # filename
                            tag[2], # ex command
                            tag[3] # fields
                        )
                    )
                else: # cursor kind tags
                    writer.writeLine(
                        '%s\t%s\t:call cursor(%d,%d)|;"\t%s' % (
                            tag[0], # tagname
                            tag[1], # filename
                            tag[2], # line number
                            tag[3], # column number
                            Collector.fields[tag[4]][0] # cursor kind
                        )
                    )

    def writeSyntaxGroups(self, writer):
        """
        Write out `self.syntaxGroups' on the provided `writer'.
        """

        for sg in self.syntaxGroups:
            if len(sg[1]) > 0:
                self.writeSyntaxGroup(writer, sg)

    def writeSyntaxGroup(self, writer, group):
        """
        Write out `group' on the provided `writer'.
        """

        writer.write("syntax keyword " + group[0])
        for kw in group[1]:
            writer.write(" " + kw)
        writer.write("\n")

    def readTagfile(self, fn):
        """
        Read input tagfile `fn' and populate `self.tags' with copies from that
        file.

        Only tags whose filename is *not* present in `Settings.inputFilenames'
        are used to populate tags. This is to make it possible to update the
        tags whose filename *is* present in `Settings.inputFilenames'.
        """

        with open(fn) as fo:
            for ln in fo:
                mo = Collector.reTagEntry.search(ln)
                if not mo is None:
                    tag = mo.group(1, 2, 3, 4)
                    if not tag[1] in Settings.inputFilenames:
                        self.addTag(tag)

    def writeTagfile(self, fn, progname):
        """
        Write output tagfile to file designated by `fn'.
        """

        if fn == "-":
            writer = WriterStdout()
        else:
            writer = WriterFile(fn)

        writer.writeLines([
'!_TAG_FILE_FORMAT\t2\t/extended format; --format=1 will not append ;" to lines/',
'!_TAG_FILE_SORTED\t%d\t/0=unsorted, 1=sorted, 2=foldcase/' % (
    1 if Settings.shouldSort else 0
),
'!_TAG_PROGRAM_AUTHOR\tZdzislaw Sliwinski\t//',
'!_TAG_PROGRAM_NAME\t%s\t//' % (progname),
'!_TAG_PROGRAM_URL\thttp://github.com/zdzislaw-s/vim-cpptags\t//'
        ])
        self.writeTags(writer)

    def writeSyntaxfile(self, fn):
        """
        Write output syntax file to file designated by `fn'.
        """

        if fn == "-":
            writer = WriterStdout()
        else:
            writer = WriterFile(fn)

        n = len(self.syntaxGroups)
        for i in range(n):
            for j in range(i + 1, n):
                self.syntaxGroups[i][1] -= self.syntaxGroups[j][1]

        self.writeSyntaxGroups(writer)

    fieldsDefs = {
        CursorKind.CLASS_DECL: ( "cursor:class", addType ),
        CursorKind.STRUCT_DECL: ( "cursor:struct", addType ),
        CursorKind.UNION_DECL: ( "cursor:union", addType ),
        CursorKind.CLASS_TEMPLATE: ( "cursor:class-template", addType ),
    }
    fields = {
        CursorKind.TYPEDEF_DECL: ( "cursor:typedef", addType ),
        CursorKind.TYPE_ALIAS_DECL: ( "cursor:type-alias", addType ),
        CursorKind.NAMESPACE: ( "cursor:namespace", addType ),
        CursorKind.ENUM_DECL: ( "cursor:enum", addType ),
        CursorKind.ENUM_CONSTANT_DECL: ( "cursor:enum-constant", addConstant ),
        CursorKind.FUNCTION_DECL: ( "cursor:function", addFunction ),
        CursorKind.CONSTRUCTOR: ( "cursor:ctor", addFunction ),
        CursorKind.DESTRUCTOR: ( "cursor:dtor", addFunction ),
        CursorKind.CXX_METHOD: ( "cursor:method", addFunction ),
        CursorKind.FIELD_DECL: ( "cursor:field", addIdentifier ),
        CursorKind.PARM_DECL: ( "cursor:param", addIdentifier ),
        CursorKind.VAR_DECL: ( "cursor:var", addIdentifier ),
        CursorKind.FUNCTION_TEMPLATE: ( "cursor:function-template", addFunction ),
    }
    fields.update(fieldsDefs)

class Writer(object):
    """
    Base class for Writer classes.
    """

    def __init__(self, fo):
        self.fileObject = fo

    def write(self, text):
        """
        Write out text to the maintained file object.
        """

        self.fileObject.write(text)

    def writeLine(self, line):
        """
        Write out `line', followed by LF.
        """

        self.write(line)
        self.fileObject.write("\n")

    def writeLines(self, lines):
        """
        Write out `lines'.
        """

        for ln in lines:
            self.writeLine(ln)

class WriterStdout(Writer):
    """
    Writer that initialises its file object to stdout.
    """

    def __init__(self):
        super(WriterStdout, self).__init__(sys.stdout)

class WriterFile(Writer):
    """
    Writer that initialises its file object with return value of open().
    """

    def __init__(self, fn):
        """
        Open `fn' and initialise the maintained file object with the returned
        value.
        """

        fo = open(fn, "w")
        super(WriterFile, self).__init__(fo)

def main(argv):
    """
    Main entry point of the script.

    Configure clang library, parse the input arguments, parse the input files,
    collect tags and write out tags and syntax groups to the specified output
    (file or stdout).

    The function exits with error code == 1 when error occurs.

    Arguments:
    argv -- array with arguments that were provided on the command line when the
            script was invoked.
    Return:
    0 -- on success
    """

    Config.set_library_file(Settings.libclangSo)
    collector = Collector()

    Settings.parseArgv(argv)

    if Settings.inputTagfile != "":
        collector.readTagfile(Settings.inputTagfile)

    args = []
    args.extend(["-" + c for c in Settings.cxxFlags])
    args.extend(["-I" + i for i in Settings.userIncludes])
    args.extend(["-I" + i for i in Settings.systemIncludes])
    args.extend(["-D" + d for d in Settings.defines])

    for filename in Settings.inputFilenames:
        sys.stderr.write(">>> Parsing: %s...\n" % (filename))

        Settings.currentFilename = filename
        index = Index.create()
        errors = []
        try:
            tu = index.parse(filename, args=args)
            errors = [
                repr(d) for d in tu.diagnostics
                    if d.severity in (
                        Diagnostic.Error,
                        Diagnostic.Fatal
                    )
            ]
            haveErrors = len(errors) > 0
        except TranslationUnitLoadError as ex:
            errors.append(ex.message)
            haveErrors = True

        if haveErrors:
            errors.insert(0, "Error: clang failed to parse '%s'" % filename)
            printErrorsAndExit(errors)

        collector.collectTags(tu)

    if not Settings.outputTagfile is None:
        collector.writeTagfile(Settings.outputTagfile, os.path.basename(argv[0]))

    if not Settings.outputSyntaxfile is None:
        collector.writeSyntaxfile(Settings.outputSyntaxfile)

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
