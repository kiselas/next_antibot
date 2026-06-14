# Security Policy

## Supported versions

Security fixes target the latest release on the `main` branch.

## Reporting a vulnerability

**Do not open a public issue for vulnerabilities.**

- Preferred: GitHub → **Security** tab → *Report a vulnerability* (private advisory).
- Or email **kisel.nf97@gmail.com** with the subject `SECURITY: antispam-bot`.

Please include the affected version/commit, reproduction steps, and impact. We aim
to respond within 7 days and to agree on a disclosure timeline.

## Notes for self-hosting

This is a self-hosted bot; the security of your instance depends on its configuration:

- **Secrets only in `.env`.** Never commit `.env` (it is git-ignored). `BOT_TOKEN`
  and your classifier API key grant full control and spend your money — treat them
  like passwords. If leaked, revoke the token via @BotFather and rotate the key.
- **Restrict chats.** Set `ALLOWED_CHAT_IDS`, otherwise the bot can be added to a
  foreign chat and consume your LLM quota (it auto-leaves non-allowed chats, but an
  explicit list is safer).
- **Daily budget.** Set `llm_daily_limit` (`/set llm_daily_limit N`) as a spend cap
  against flooding/raids.
- **Admins by ID.** Prefer `ADMIN_USER_IDS` (immutable) over `ADMIN_USERNAMES`
  (usernames can be changed/released).
- **Privacy.** The bot stores suspicious message text in a local SQLite log and
  writes it to INFO-level logs (for false-positive review). Restrict access to
  `./data` and raise `LOG_LEVEL` if needed.
