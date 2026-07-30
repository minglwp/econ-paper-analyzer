#!/bin/zsh
set -e
cd "${0:A:h}"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

EPA_PORT="${EPA_PORT:-8765}"
while lsof -nP -iTCP:"$EPA_PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  EPA_PORT=$((EPA_PORT + 1))
done

mkdir -p .runtime/matplotlib .runtime/cache
export PYTHONPATH="$PWD"
export MPLCONFIGDIR="$PWD/.runtime/matplotlib"
export XDG_CACHE_HOME="$PWD/.runtime/cache"

EPA_URL="http://127.0.0.1:$EPA_PORT"
echo "经管论文数据自动处理器：$EPA_URL"
(sleep 1; open "$EPA_URL") &
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$EPA_PORT"
