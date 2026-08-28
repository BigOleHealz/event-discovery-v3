#!/bin/bash
source .env

curl --fail --silent \
  -H "Authorization: Bearer ${EVENTBRITE_API_TOKEN}" \
  "https://www.eventbriteapi.com/v3/users/me/organizations/" \
  | python3 -m json.tool




# curl --fail --silent \
#   -H "Authorization: Bearer ${EVENTBRITE_API_TOKEN}" \
#   "https://www.eventbriteapi.com/v3/organizations/123456789/events/" \
#   | python3 -m json.tool