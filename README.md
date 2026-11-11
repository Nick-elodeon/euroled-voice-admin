# euroled-voice-admin

Telegram admin for the EUROLED ElevenLabs voice agent: edit the prompt, first message and knowledge base from chat, and get an instant summary after every call.

ElevenLabs stays the source of truth; the bot is a thin editor over its API, so a free host with an ephemeral disk is fine.

## Run

1. Set the variables from `.env.example` in the hosting platform (secrets only there, never in git).
2. Deploy with the included `Dockerfile` (start command `python bot/main.py`, listens on `$PORT`).
3. In ElevenLabs, set the post-call webhook to `https://<host>/elevenlabs/post-call` and copy its signing secret into `ELEVEN_WEBHOOK_SECRET`.
4. Open the bot, send `/start`. An unknown user sees their Telegram ID to add to `ALLOWED_USER_IDS`.

## Commands

`/prompt`, `/rule`, `/setprompt`, `/rollback`, `/first`, `/setfirst`, `/kb`, `/kbadd`, `/kbdel`. Every change asks for confirmation and sends the previous prompt back as a file (backup that survives restarts). Under each call summary there is an "Error" button that turns a remark into a prompt rule.

## Roadmap

Scenario tests before publishing, weekly euroled.lv catalog sync by category, caller memory.
