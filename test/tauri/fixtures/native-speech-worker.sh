#!/bin/sh
# Synthetic worker for Linux desktop integration tests; never captures audio.
printf '%s\n' '{"type":"ready","protocol":1}' '{"type":"listening"}'
IFS= read -r command
[ "$command" = '{"op":"stop"}' ] || exit 9
printf '%s\n' '{"type":"segment","segment":{"start_sample":0,"end_sample":480,"generation":0,"samples":480,"voiced_frames":1,"segment_frames":1,"rms_dbfs":-12,"accepted":true,"recognition":{"status":"recognized","text":"明日の会議は十時からです","punctuation":{"status":"applied","text":"明日の会議は十時からです。","processing_ms":1}}}}' '{"type":"stopped"}'
