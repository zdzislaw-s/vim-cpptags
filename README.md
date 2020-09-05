Generate tagfile for C++ source code
====================================

TODO: add some text here

Help screen:
```
usage: vim-cpptags.py [-h] [--libclang <filename>] [-S]
                      [-c <compilation flag>] [-d <define>] [-I <include dir>]
                      [-i <include dir>] [-Y] [-C] [-o <filename>]
                      [-t <filename>]
                      <input file> [<input file> ...]

Generate tagfile for C++ source code.

positional arguments:
  <input file>          Input C++ source file.

optional arguments:
  -h, --help            show this help message and exit
  --libclang <filename>
                        Full pathname to libclang.so file (default:
                        '/usr/lib/llvm-3.8/lib/libclang.so.1').
  -S, --no-sort         Don't sort the output tagfile.
  -c <compilation flag>
                        Clang compilation flag (e.g.: std=c++14, fPIC). Can be
                        specified multiple times.
  -d <define>           Define that is passed to clang as the -D option (e.g.
                        TRACE, MT_FLAG=1). Can be specified multiple times.
  -I <include dir>      User include directory that is passed to clang as the
                        -I option. Can be specified multiple times.
  -i <include dir>      System include directory that is passed to clang as
                        the -I option. Can be specified multiple times.
  -Y, --no-collect-system-includes
                        Don't collect tags from system includes. For the
                        option to be in effect, the system include directory
                        has to be specified with the '-i' option.
  -C, --no-use-ctags    Don't use ctags to collect macro definitions.
  -o <filename>         Output filename. The tags are written to stdout if
                        this option is not specified.
  -t <filename>, --tagfile <filename>
                        Input tagfile. The purpose of this option is to allow
                        incremental updates of tagfiles. When this option is
                        specified the collection of tags is limited to the
                        input C++ source files exclusively, i.e. the logic for
                        collecting tags from files that are directly or
                        indirectly #included is disabled.
```

Example of command line invocation:
```bash
$ vim-cpptags.py \
    -c std=c++14 \
    -c fPIC \
    -i /usr/include \
    -i /usr/lib \
    -i /usr/lib/llvm-3.8/lib/clang/3.8.0/include \
    -i /usr/include/x86_64-linux-gnu/qt5 \
    -i /usr/include/x86_64-linux-gnu/qt5/QtCore \
    -i /usr/include/x86_64-linux-gnu/qt5/QtGui \
    -i /usr/include/x86_64-linux-gnu/qt5/QtWidgets \
    -I include \
    -d MT_FLAG=1 \
    -d APPLICATION_TYPE_QT \
    -d TRACE \
    -d TRACE_FD=0 \
    -o tags \
    *.cpp
```
