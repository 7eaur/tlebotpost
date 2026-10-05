# Railway Deployment Record

## Current deployment target

- Repository: `7eaur/tlebotpost`
- Railway project: `authentic-ambition`
- Service: `tlebotpost`
- Environment: `production`
- Source branch: `main`
- Root directory: `/backend`
- Dockerfile: `/backend/Dockerfile`
- Start command: `python -m app.main`
- Persistent volume: `tlebotpost-data` mounted at `/app/data` (500 MB)

## What was configured

The Railway service was connected to the repository and configured to deploy from `main`. Required runtime secrets were added to Railway as environment variables; secret values are intentionally not recorded in Git.

Required runtime variables:
- `API_ID`
- `API_HASH`
- `BOT_TOKEN`
- `OWNER_ID`

Branding variables:
- `BRAND_FOOTER`
- `BRAND_LINK`

## Code changes made for deployment

1. Removed the unsupported Docker volume declaration from the application Dockerfile.
2. Added an owner startup notification: `✅ البوت يعمل الآن وجاهز لاستقبال الأوامر.`
3. Made branding optional at runtime; branding can still be configured through environment variables.
4. Added a guided Arabic control-bot flow for login, source creation, target configuration, and cancellation.
5. Added normalization for `https://t.me/...`, `t.me/...`, `https://t.me/c/...`, international phone numbers, Arabic digits, and login codes.
6. Added user-facing error messages instead of exposing only Python exception names.
7. Source/target resolution now connects the user session automatically after login, without requiring `/run` first.

## Deployment history

- Initial Railway deployment attempts on the baseline commit failed.
- `8bee14aae30bdba2703c4d06330eb1011ba44d3a` recorded the Railway baseline.
- `efd1b8e78eb62497c8a17d52e0e237e4e912c656` fixed the Docker volume configuration.
- `67a185c218835fe18a73bf7351686244bca3291f` added the owner startup notification.
- `d78fc904c64b3a41dec2043fb66e531f263d9b05` made branding optional.
- The latest Railway deployment is being triggered from `main`.

## Verification status

The latest previously completed Railway deployment was:
- Deployment: `3f424373-25b0-4f84-9af0-6d2833167b25`
- Commit: `daf47f53d4e03e2fa5b37b8886aaf529f723e228`
- Status: `SUCCESS`

A further runtime/storage fix was then committed on `main`:
- Commit: `36379d35728180f279964ca8b3763014868a04d3`
- Change: simplify the container runtime user setup so the Railway-mounted `/app/data` volume can be written by the application.
- A new Railway deployment was triggered: `4407b028-9e04-4b5a-a135-dd3482a288d3`
- At the time of this record update, that deployment was still `INITIALIZING`.

Important source-of-truth note:
- `backend/app/config.py` on `main` still contains the `BRAND_FOOTER or BRAND_LINK` validation. Railway therefore must continue to receive a non-empty `BRAND_FOOTER` (or `BRAND_LINK`). The Railway service has `BRAND_FOOTER` configured.
- `backend/app/control/bot.py` contains the owner startup-ready notification.
- `backend/Dockerfile` now creates `/app/data` without switching to the previous non-root runtime user.

### Final verification checklist

Do not mark production fully verified until the current deployment reaches a stable running state and runtime logs confirm:

1. `python -m app.main` starts without configuration or database errors.
2. Telegram polling starts successfully.
3. The owner startup-ready notification is attempted successfully.
4. `/app/data` is writable and persistent across restart.
5. No restart/crash loop occurs.
6. Basic owner commands such as `/start` and `/status` respond.

### Recommended first-run sequence

1. Open the control bot and press `تسجيل جلسة الحساب`.
2. Send the phone number in international format, for example `+967700000000`.
3. Send the Telegram login code. If two-step verification is enabled, send the code followed by the password.
4. Press `إضافة مصدر`, then send `@channel` or a full `https://t.me/channel` link.
5. Press `القناة الهدف`, then send the target channel reference.
6. Add the control bot as an administrator in the target channel with `Post Messages` permission.
7. Press `تشغيل`.

The bot starts from the latest message when a source is added. It does not publish historical messages.

### Troubleshooting

- **Phone rejected:** use the full international form with `+` or `00`, without a local leading zero.
- **Source cannot be resolved:** confirm that the user account is already a member of the private source and that the public link is valid.
- **Target cannot be resolved:** confirm that the user account can open the channel, then confirm the bot is an administrator with `Post Messages`.
- **No login flow is pending:** press `تسجيل جلسة الحساب` again; `/cancel` clears an incomplete flow.
- **System refuses to start:** check that at least one source and one target are configured.

## Security

Secrets remain in Railway variables and are not committed to Git. The credentials used for the initial deployment test should be rotated/reissued after verification.
