#!/usr/bin/env bash
# 打包 ShopAdvisor：zip 根目录必须直接是 manifest.json / mod_main.gd（不能套一层 mod/）。
# 打包前先跑本机回归（决策引擎同构检查 + 用例），不通过就不出包。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOD_DIR="$ROOT/mod"
OUT_DIR="$ROOT/dist"
ZIP_NAME="BrotatoHelper-ShopAdvisor.zip"

echo "==> 回归测试"
python3 "$ROOT/tools/check_parity.py"
python3 "$ROOT/tools/check_gd.py"
python3 "$ROOT/tools/check_inputs.py"
python3 "$ROOT/tools/check_special_coverage.py"
python3 "$ROOT/tools/calibrate.py" --check
python3 "$ROOT/tools/evaluate.py" > /dev/null
python3 "$ROOT/tools/check_gd_diff.py"
python3 "$ROOT/tools/ingest_debug.py" --selftest
python3 -c "import json,sys; [json.load(open(p, encoding='utf-8')) for p in sys.argv[1:]]" \
  "$MOD_DIR/manifest.json" "$MOD_DIR/data/priors.json" "$MOD_DIR/data/brotato_data.json"

for required in manifest.json mod_main.gd scripts/advisor.gd scripts/state_collector.gd \
                scripts/shop_advisor.gd extensions/ui/menus/shop/shop_items_container.gd \
                extensions/ui/menus/shop/shop_item.gd data/brotato_data.json data/priors.json; do
  [ -f "$MOD_DIR/$required" ] || { echo "缺少 $required" >&2; exit 1; }
done

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR/$ZIP_NAME"

if command -v zip > /dev/null 2>&1; then
  ( cd "$MOD_DIR" && zip -r -q "$OUT_DIR/$ZIP_NAME" . -x '*.DS_Store' -x '__pycache__/*' )
else
  python3 - "$MOD_DIR" "$OUT_DIR/$ZIP_NAME" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
skip_dirs = {"__pycache__", ".git"}
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in sorted(files):
            if name.endswith((".pyc", ".DS_Store")):
                continue
            full = os.path.join(root, name)
            z.write(full, os.path.relpath(full, src))
print("wrote", dst)
PY
fi

echo "==> 产物"
ls -l "$OUT_DIR/$ZIP_NAME"
python3 -c "import zipfile,sys; print('\n'.join(sorted(zipfile.ZipFile(sys.argv[1]).namelist())))" "$OUT_DIR/$ZIP_NAME"
echo "把 $OUT_DIR/$ZIP_NAME 放进游戏的 mods/ 目录即可（详见 README.md）"
