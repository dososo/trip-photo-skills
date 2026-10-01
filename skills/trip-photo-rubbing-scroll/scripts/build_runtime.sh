#!/bin/sh
# 重新打包交互页运行时。只有改了 assets/runtime/src/ 才需要跑；仓库里已附打包好的 scroll.bundle.js。
# 需要 Node.js 18+；esbuild 由 npx 临时下载，不写进项目。
set -e
cd "$(dirname "$0")/../assets/runtime"
npx --yes esbuild@0.25.10 src/app.js --bundle --format=iife --minify --target=es2020 \
  --alias:three=./vendor/three --legal-comments=eof --outfile=scroll.bundle.js
