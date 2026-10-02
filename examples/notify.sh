#!/bin/sh
# Hook-friendly one-liners (CI, cron, Home Assistant rest_command, git hooks...).
#   LD_URL=http://display:8099 LD_TOKEN=secret ./examples/notify.sh ok "Deploy finished"
URL=${LD_URL:-http://127.0.0.1:8099}
AUTH=${LD_TOKEN:+-H "Authorization: Bearer $LD_TOKEN"}
case "$1" in
  ok)   R=excited; E=joy ;;
  fail) R=cry;     E=sad ;;
  warn) R=nervous; E=nervous ;;
  *)    R=surprise; E=neutral ;;
esac
curl -s $AUTH -X POST "$URL/api/op" -H 'Content-Type: application/json' -d "[
  {\"op\":\"react\",\"name\":\"$R\"},
  {\"op\":\"say\",\"text\":\"$2\",\"expr\":\"$E\"}
]" > /dev/null
