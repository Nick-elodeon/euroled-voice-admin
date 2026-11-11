# euroled-voice-admin

Telegram admin for the EUROLED ElevenLabs voice agent: edit the prompt, first message and knowledge base from chat, and get an instant summary after every call.

ElevenLabs stays the source of truth; the bot is a thin editor over its API, so a free host with an ephemeral disk is fine.

## Run

1. Set the variables from `.env.example` in the hosting platform (secrets only there, never in git).
2. On a VM: `sudo bash deploy/setup.sh`, fill `/etc/euroled-admin.env`, `systemctl restart euroled-admin`. Or use the `Dockerfile` on a PaaS.
3. Post-call summaries are polled from the ElevenLabs API every minute (no open port needed). Optional webhook mode: set `ELEVEN_WEBHOOK_SECRET` and point ElevenLabs to `/elevenlabs/post-call`.
4. Open the bot, send `/start`. An unknown user sees their Telegram ID to add to `ALLOWED_USER_IDS`.

## Commands

`/prompt`, `/rule`, `/setprompt`, `/rollback`, `/first`, `/setfirst`, `/kb`, `/kbadd`, `/kbdel`. Every change asks for confirmation and sends the previous prompt back as a file (backup that survives restarts). Under each call summary there is an "Error" button that turns a remark into a prompt rule.

## Roadmap

Scenario tests before publishing, weekly euroled.lv catalog sync by category, caller memory.
