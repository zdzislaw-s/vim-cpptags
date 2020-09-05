#!/usr/bin/env python2

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
    shouldIncludeSystemIncludes = True
    shouldUseCtags = True
    outputFilename = ""
    inputFilenames = []
    fieldsDefs = {
        CursorKind.CLASS_DECL: "class-def",
        CursorKind.ENUM_CONSTANT_DECL: "enum-constant-def",
        CursorKind.ENUM_DECL: "enum-def",
        CursorKind.FIELD_DECL: "field-def",
        CursorKind.FUNCTION_DECL: "function-def",
        CursorKind.PARM_DECL: "param-def",
        CursorKind.STRUCT_DECL: "struct-def",
        CursorKind.TYPE_ALIAS_DECL: "type-alias",
        CursorKind.TYPEDEF_DECL: "typedef-def",
        CursorKind.UNION_DECL: "union-def"
    }
    fields = {
        CursorKind.CLASS_TEMPLATE: "class-template",
        CursorKind.CONSTRUCTOR: "ctor",
        CursorKind.CXX_METHOD: "method",
        CursorKind.DESTRUCTOR: "dtor",
        CursorKind.FUNCTION_TEMPLATE: "function-template",
        CursorKind.VAR_DECL: "var-decl"
    }
    fields.update(fieldsDefs)

    @staticmethod
    def parseArgv(argv):
        """
        Initialise settings with values from `argv'.
        """

        parser = argparse.ArgumentParser(
            description="Generate tagfile for C++ source code."
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
            "-Y", "--no-include-system-includes",
            action="store_true",
            default=not Settings.shouldIncludeSystemIncludes,
            help="Dont't include in the output tagfile the tags that were discovered while processing system includes. For the option to be in effect, the system include directory has to be specified with the '-i' option."
        )
        parser.add_argument(
            "-C", "--no-use-ctags",
            action="store_true",
            default=not Settings.shouldUseCtags,
            help="Dont't use ctags to discover macro definitions."
        )
        parser.add_argument(
            "-o",
            dest="output_filename",
            metavar="<filename>",
            default=Settings.outputFilename,
            help="Output filename. The tags are written to stdout if this option is not specified."
        )
        parser.add_argument(
            "filenames",
            metavar="<input file>",
            nargs="+",
            default=[],
            help="Input C++ source file."
        )
        args = parser.parse_args(argv[1:])

        Settings.libclangSo = args.libclang
        Settings.shouldSort = not args.no_sort
        Settings.cxxFlags = args.cxx_flags
        Settings.defines = args.defines
        Settings.userIncludes = args.user_includes
        Settings.systemIncludes = args.system_includes
        Settings.shouldIncludeSystemIncludes = not args.no_include_system_includes
        Settings.shouldUseCtags = not args.no_use_ctags
        Settings.outputFilename = args.output_filename
        Settings.inputFilenames = args.filenames

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

    def __init__(self):
        self.tags = []

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
            (child.kind in Settings.fields) and
            (
                next(
                    (
                        si for si in Settings.systemIncludes
                            if child.location.file.name.startswith(si)
                    ),
                    None
                ) is None
                if not Settings.shouldIncludeSystemIncludes else True
            ) and
            (len(child.spelling) > 0) and
            (child.is_definition() if child.kind in Settings.fieldsDefs else True) and
            (
                (
                    child.semantic_parent.kind == CursorKind.TRANSLATION_UNIT or
                    child.semantic_parent.kind == CursorKind.CLASS_DECL or
                    child.semantic_parent.kind == CursorKind.STRUCT_DECL
                )
                if child.kind == CursorKind.VAR_DECL else True
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
            filename = child.location.file.name
            tag = (
                child.spelling,
                filename,
                child.location.line,
                child.location.column,
                child.kind
            )
            self.addTag(tag)

            tag = (os.path.basename(filename), filename)
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

            reTag = re.compile('^([^\t]+)\t([^\t]+)\t([^\t]+);"\t.*$')
            for ln in out.split("\n"):
                mo = reTag.search(ln)
                if not mo is None:
                    tag = mo.group(1, 2, 3)
                    self.addTag(tag)

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
            #print tag
            if not tag in self.tags:
                self.tags.append(tag)

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
                        '%s\t%s\t1;"\tfile' % (
                            tag[0], # basename
                            tag[1] # filename
                        )
                    )
                elif len(tag) == 3: # macro definition tag
                    writer.writeLine(
                        '%s\t%s\t%s;"\tmacro' % (
                            tag[0], # macro name
                            tag[1], # ex command
                            tag[2] # line number
                        )
                    )
                else: # other tags
                    writer.writeLine(
                        '%s\t%s\t:call cursor(%d,%d)|;"\t%s' % (
                            tag[0], # tagname
                            tag[1], # filename
                            tag[2], # line number
                            tag[3], # column number
                            Settings.fields[tag[4]] # cursor kind
                        )
                    )

class Writer(object):
    """
    Base class for Writer objects.
    """

    def __init__(self, fo):
        self.fileObject = fo

    def writeLine(self, line):
        """
        Write out `line', followed by LF, to the maintained file object.
        """

        self.fileObject.write(line)
        self.fileObject.write("\n")

    def writeLines(self, lines):
        """
        Write out `lines' to the maintained file object.
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
    collect tags and write out the tags to the specified output (file or
    stdout).

    The function exits with error code == 1 when error occurs.

    Arguments:
    argv -- array with arguments that were provided on the command line when the
            scipt was invoked.
    Return:
    0 -- on success
    """

    Config.set_library_file(Settings.libclangSo)
    collector = Collector()

    Settings.parseArgv(argv)

    args = []
    args.extend(["-" + c for c in Settings.cxxFlags])
    args.extend(["-I" + i for i in Settings.userIncludes])
    args.extend(["-I" + i for i in Settings.systemIncludes])
    args.extend(["-D" + d for d in Settings.defines])

    for filename in Settings.inputFilenames:
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

    if Settings.outputFilename == "":
        writer = WriterStdout()
    else:
        writer = WriterFile(Settings.outputFilename)

    writer.writeLines([
'!_TAG_FILE_FORMAT\t2\t/extended format; --format=1 will not append ;" to lines/',
'!_TAG_FILE_SORTED\t%d\t/0=unsorted, 1=sorted, 2=foldcase/' % (
    1 if Settings.shouldSort else 0
),
'!_TAG_PROGRAM_AUTHOR\tZdzislaw Sliwinski\t//',
'!_TAG_PROGRAM_NAME\t%s\t//' % (os.path.basename(argv[0])),
'!_TAG_PROGRAM_URL\thttp://github.com/zdzislaw-s/vim-ctags\t//'
    ])

    collector.writeTags(writer)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
