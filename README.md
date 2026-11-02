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

## Text from audio (dev_helper/new_file)

Run `python dev_helper/new_file/new_txt_from_audio_GUI.py`.

A small Tk workbench for audio transcripts. It lists the audio files of a
folder on the left (a check mark means the companion `<name>.txt` already
exists) and shows that text file in an editor on the right. Play/pause,
`-10 s` and a progress slider work without extra packages (pygame, Windows
MCI or ffplay/mpv/afplay are used when present). **Run
new_txt_from_audio.py** executes the sibling transcriber for the selected
file, or for every file that still has no text, and the new text is loaded
automatically.

For karaoke-style syncing, Ctrl+click a word while the audio plays to pin
it to the current time; playback then highlights the current word. Marks
can also be estimated or imported from Whisper-style JSON or SRT/VTT and
are stored in `<name>.sync.json`. See
[dev_helper/new_file/README.md](dev_helper/new_file/README.md) for the
transcriber contract, supported backends and the investigation of
automatic word alignment.
