#!/bin/sh
# Push an ambient card from anything (cron, Home Assistant rest_command, a sensor script).
# Cards don't wake the display; they join the idle rotation until their ttl expires.
#   ./examples/push_card.sh garage "{home}Garage" "Door open {warn}" nervous 900
URL=${LD_URL:-http://127.0.0.1:8099}
AUTH=${LD_TOKEN:+-H "Authorization: Bearer $LD_TOKEN"}
curl -s $AUTH -X POST "$URL/api/card" -H 'Content-Type: application/json' \
  -d "{\"id\":\"$1\",\"title\":\"$2\",\"text\":\"$3\",\"mood\":\"${4:-neutral}\",\"ttl\":${5:-600}}"
