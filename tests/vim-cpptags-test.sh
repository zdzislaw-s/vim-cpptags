if [ ! -d out ]; then
    mkdir out
fi

for fn in *.cpp; do
    ../vim-cpptags.py $fn -S -o out/$fn.tags -s out/$fn.syntax
    if ! diff ref/$fn.tags out/$fn.tags; then
       echo "out/$fn.tags differs from ref"
    fi
    if ! diff ref/$fn.syntax out/$fn.syntax; then
       echo "### Fail: out/$fn.syntax differs from ref"
    fi
done
