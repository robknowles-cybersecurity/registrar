#!/bin/bash
# Run every test file and print a one-line summary per file.
cd "$(dirname "$0")/.." || exit 2
total=0; failed=0
for t in tests/test_*.py; do
  out=$(python3 "$t" 2>&1)
  line=$(echo "$out" | grep -E "^Ran [0-9]+ test" | tail -1)
  verdict=$(echo "$out" | tail -1)
  n=$(echo "$line" | awk "{print \$2}")
  total=$((total + n))
  [ "$verdict" = "OK" ] || failed=$((failed + 1))
  printf "%-38s %-24s %s\n" "$(basename $t)" "$line" "$verdict"
done
echo "----"
echo "$total tests across $(ls tests/test_*.py | wc -l) files; $failed file(s) not OK"
