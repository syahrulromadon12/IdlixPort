#!/bin/sh
project=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
if [ ! -x "$project/.venv/bin/idlixport" ]; then
    printf '%s\n' 'Siapkan .venv dan instal IdlixPort sesuai README terlebih dahulu.' >&2
    exit 1
fi
exec "$project/.venv/bin/idlixport" web --open-browser "$@"
