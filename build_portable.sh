#!/usr/bin/env bash
# Build the portable, unzip-and-run Windows folder — entirely from macOS/Linux.
#
# Produces dist/PlanGrab/ containing:
#   python/   relocatable CPython for Windows x64 (tcl/tk STRIPPED — the folder
#             picker uses a native PowerShell dialog, so tcl/tk isn't needed;
#             dropping it removes thousands of files that made the zip slow to
#             extract on locked-down PCs)
#   lib/      all dependencies as Windows wheels (pydantic-core, …)
#   plangrab/ config.toml  Run.ps1  README.md
# …and zips it to dist/PlanGrab-win64.zip for cloud-sync distribution.
#
# Nothing here needs admin on the target PC: it runs in place via Run.ps1.
#
# >>> 30-SECOND SMOKE TEST (run on the locked-down target PC first) <<<
#   Unzip, open PowerShell in the folder, run:  .\python\python.exe --version
#   If it prints a version, the whole portable approach is confirmed — run Run.ps1.
#   Only if app-allowlisting blocks that exe do you need the PowerShell-native
#   fallback (see README).
set -euo pipefail
cd "$(dirname "$0")"

# A real, current python-build-standalone Windows build (override if you like).
PY_URL="${PY_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.12.13%2B20260623-x86_64-pc-windows-msvc-install_only.tar.gz}"
PY_TAG="cp312"          # must match the CPython minor above (3.12 -> cp312)
PY_MINOR="3.12"

DIST="dist/PlanGrab"
echo "==> Clean dist/"
rm -rf "$DIST" dist/PlanGrab-win64.zip
mkdir -p "$DIST"

echo "==> Download relocatable CPython for Windows"
TARBALL="dist/_cpython-win64.tar.gz"
curl -fL --retry 3 -o "$TARBALL" "$PY_URL"
echo "==> Extract CPython (-> $DIST/python)"
tar -xzf "$TARBALL" -C "$DIST"     # extracts a top-level python/ directory
rm -f "$TARBALL"
test -f "$DIST/python/python.exe" || { echo "ERROR: python.exe not found after extract"; exit 1; }

echo "==> Slim the runtime: drop debug symbols + dev-only stdlib (not used at runtime)"
# ~58 MB of Windows .pdb debug symbols (libcrypto/python312/etc.) and dev-only
# stdlib modules.
find "$DIST/python" -name '*.pdb' -delete
rm -rf "$DIST/python/Lib/ensurepip" "$DIST/python/Lib/idlelib" \
       "$DIST/python/Lib/lib2to3" "$DIST/python/Lib/turtledemo" \
       "$DIST/python/Lib/pydoc_data" "$DIST/python/include"

echo "==> Drop tcl/tk (thousands of tiny files): the folder picker is now a"
echo "    native PowerShell dialog, so tkinter/tcl are no longer needed."
rm -rf "$DIST/python/tcl" "$DIST/python/Lib/tkinter"
# The _tkinter extension + tcl/tk DLLs live in DLLs/ (sometimes the runtime root)
# depending on the build; search the whole tree with DLL-specific patterns so we
# catch them wherever they sit, without matching unrelated files.
find "$DIST/python" \( -iname '_tkinter*' -o -iname 'tcl*.dll' -o -iname 'tk*.dll' \) \
    -delete 2>/dev/null || true

echo "==> Vendor dependencies as Windows wheels (-> $DIST/lib)"
python3 -m pip install --target "$DIST/lib" \
  --platform win_amd64 --python-version "$PY_MINOR" --implementation cp --abi "$PY_TAG" \
  --only-binary=:all: -r requirements.txt

echo "==> Copy application files"
cp -R plangrab "$DIST/plangrab"
cp -R tools "$DIST/tools"          # dashboard's "Re-run checks" imports tools.smoke_test
cp config.toml Run.ps1 Run-Check.ps1 README.md "$DIST/"
# Ship the registry + last-known status; skip caches/logs.
mkdir -p "$DIST/data"
cp data/lpa_registry.csv "$DIST/data/" 2>/dev/null || true
cp data/compat_status.json "$DIST/data/" 2>/dev/null || true
cp data/lpa_boundaries.json "$DIST/data/" 2>/dev/null || true   # dashboard coverage map
cp data/lpa_systems.csv "$DIST/data/" 2>/dev/null || true       # map: colour by planning system
# Drop caches so the synced folder stays lean.
find "$DIST" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "==> Zip for distribution (max compression -> smaller download)"
( cd dist && zip -9 -qr PlanGrab-win64.zip PlanGrab )
echo ""
echo "Done -> dist/PlanGrab-win64.zip"
echo "On the target PC: unzip, then either run the smoke test above or just run Run.ps1."
