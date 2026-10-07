# github_helper

A small Tkinter GUI for common Git operations, plus a separate GitHub 2FA helper.

## Portable Git Assistant

Run `python github_helper.py`. The app supports clone, fetch, commit, push,
patch application, and branch operations. Git network operations first try
without the app token, so public repositories and SSH-key authentication do
not require `t.txt` or `t.bin`. If a remote actually requests HTTPS
credentials, the app loads `t.txt` or unlocks `t.bin` and retries.

The **Encrypt Token** button encrypts a token from `t.txt` into `t.bin`.

## GitHub 2FA helper

Run `python 2FA_helper.py` and paste the Base32 authenticator secret shown
by GitHub (usually 32 characters). **Encrypt & Save Secret** stores it in an
encrypted `secret.bin`; the window then displays the current six-digit code
and refresh countdown. Use **Copy pass key** to copy the code to the clipboard.
Codes refresh every 30 seconds.

The first save asks you to create a password. Later runs ask for that
password to unlock `secret.bin`. For a personal copy of the script, you may
set `HARDCODED_PASSWORD` near the top of `2FA_helper.py`; anyone who can read
the script can then read that value, so leaving it blank is safer. The helper
installs `cryptography` into the local `libs` folder if it is not already
available.

Local credential files (`t.txt`, `t.bin`, and `secret.bin`) are ignored by
Git. Do not commit these files or remove their ignore rules.

## repo
https://github.com/Yuki2ka/github_helper
