#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/rdagent/bin:$PATH"
mkdir -p /root/rdagent-work
cd /root/rdagent-work
cat > .env <<EOF
CHAT_MODEL=openai/glm-5.3-flash
CHAT_API_KEY=${CHAT_KEY}
CHAT_API_BASE=https://open.bigmodel.cn/api/coding/paas/v4
EOF
chmod 600 .env
echo "env written (key masked): $(grep -c CHAT_MODEL .env) entries"
/opt/rdagent/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/root/rdagent-work/.env")
import os
import litellm
resp = litellm.completion(
    model="openai/glm-5.3-flash",
    messages=[{"role": "user", "content": "1+1=?只回复数字"}],
    max_tokens=512,
    api_key=os.environ["CHAT_API_KEY"],
    base_url="https://open.bigmodel.cn/api/coding/paas/v4",
)
print("chat reply:", (resp.choices[0].message.content or "").strip()[:40])
PY
