# Text from audio — companion editor

`new_txt_from_audio_GUI.py` is a Tkinter front end for turning a folder of
audio files into companion `.txt` transcripts, and for reviewing them while
the audio plays.

```
+-- audio folder ------------------------------------------------+
|  Open folder...            [audio file list]     |  song.txt  |
|  filter: [........]                              |  editor    |
|  song.mp3        check  3.1 MB                   |            |
|  notes.m4a              1.2 MB                   |  Ctrl+click|
|                                                  |  sync bar  |
|  Playback: [Play] [-10 s] [Stop]  1:12 / 3:45    |            |
|  |||||||||||-------------                        |            |
|                                                  |            |
|  Transcript: [Run new_txt_from_audio.py]         |            |
|  [ ] Missing texts only (whole folder)           |            |
|  log output...                                   |            |
+--------------------------------------------------+------------+
```

## Run

```
python new_txt_from_audio_GUI.py [folder]
```

The folder argument is optional; without it the GUI starts in its own
folder and waits for **Open folder...**.

No third-party package is needed to edit text. Audio playback uses the
first backend that is available:

| Platform | Backend | Seek | Pause |
| --- | --- | --- | --- |
| any with pygame | `pygame.mixer.music` | yes | yes |
| Windows | MCI (`mciSendString`) | yes | yes |
| Linux/macOS | `ffplay` or `mpv` | yes | yes (POSIX signal) |
| macOS | `afplay` | no | yes (POSIX signal) |
| Linux | `paplay`/`aplay` (wav only) | no | yes (POSIX signal) |

**Tools -> Install audio support (pygame)** installs `pygame` and `mutagen`
into `libs/` next to the script (portable, never touches the OS Python).
`mutagen` only makes duration detection fast; without it, durations come
from `wave`/`ffprobe`/pygame.

If no backend exists, the list, the transcriber button, saving and the
sync editor still work; only playback is disabled.

## The companion text file and the transcriber

Every audio file maps to `<audio name>.txt` in the same folder, so
`song.mp3` -> `song.txt`. A green check in the list marks audio files that
already have their text.

**Run new_txt_from_audio.py** executes the sibling script
`new_txt_from_audio.py` (override by locating it in the file dialog if it
is somewhere else) and streams its output into the log. Two constants at
the top of the GUI file control the call:

```python
TRANSCRIBER_SCRIPT = APP_DIR / "new_txt_from_audio.py"
TRANSCRIBER_ARGS = ["{audio}"]
```

`{audio}`, `{folder}` and `{out}` are replaced with the selected audio
file, its folder and the expected text path. The default contract is
therefore:

```
python new_txt_from_audio.py /path/to/song.mp3
# writes /path/to/song.txt
```

If your transcriber only scans its own folder, set
`TRANSCRIBER_ARGS = []`. With **Missing texts only (whole folder)**
checked, the GUI calls the script once per audio file that has no `.txt`
yet, and stops after each file to refresh the list and log.

When the run finishes, the list is refreshed and the new text file is
loaded into the editor automatically (unless the editor has unsaved
changes). If the transcriber also writes timings (see below), they are
imported as sync marks as soon as the text is loaded.

## Karaoke sync

1. Load an audio file and its text.
2. Press **Play**.
3. **Ctrl+click** (Command+click on macOS) the word you hear *right now*.
   A mark with the current playback time is stored on that word.
4. Continue through the text. The highlighted word follows the clock while
   the audio plays; marks are shown with a light blue background.
5. **Ctrl+right-click** a word to delete its mark, **Clear marks** to
   start over.

Notes:

* A human presses slightly after hearing the word, so the time of a live
  Ctrl+click is reduced by `CTRL_CLICK_LEAD_SECONDS` (default 0.25 s).
  Set it to `0` if you do not want the compensation.
* Marks are Tk text marks, so they move with their word when you edit or
  insert text during the session. Offsets are written to disk on save.
* **Follow playback** (on by default) scrolls the editor to the current
  word and shows it in the status bar. Turn it off to read without the
  editor jumping.
* Clicking with the *left* button still places the text cursor as usual;
  only Ctrl/Command+click adds marks.

### Storing marks

Marks live in `<audio name>.sync.json` beside the audio:

```json
{
  "version": 1,
  "audio": "song.mp3",
  "text": "song.txt",
  "marks": [
    { "time": 0.0, "offset": 0, "word": "Once" },
    { "time": 0.62, "offset": 5, "word": "upon" }
  ]
}
```

`offset` is a character offset into the `.txt` file (`time` is seconds).
The sidecar is rewritten whenever marks change and deleted when the last
mark is removed, so it is safe to version next to the text.

### Automatic timings

* **Auto-estimate** spreads marks over the whole file, weighted by word
  length (a lead-in and tail are left out). It is a usable starting point
  for sentence-level highlighting but is not real alignment.
* **Import timings...** reads whisper-style JSON (`segments[].words[]`,
  `word_segments`, faster-whisper/whisper-timestamped/stable-ts output),
  SRT and WebVTT. Timed words are matched against the text in order, so
  punctuation and capitalisation differences are tolerated; unmatched
  words are counted in the status bar.
* If the transcriber writes `<audio>.json`, `<audio>.words.json`,
  `<audio>.srt` or `<audio>.vtt`, the GUI imports it automatically after
  the run (and when loading a file whose sidecar has no marks yet).

## Automatic alignment — what is realistic

The manual gesture above is the only method that is always correct without
extra models, but it is also the slowest for long files. The options that
were evaluated:

| Approach | Granularity | Extra requirements | Effort | Verdict |
| --- | --- | --- | --- | --- |
| Manual Ctrl+click + proportional estimate (implemented) | word / segment | none | done | reliable baseline, edits instantly |
| Word timestamps from the transcriber (Whisper `word_timestamps`, faster-whisper, whisper.cpp `-ml 1 --output-json-full`, whisper-timestamped, stable-ts) | word | the model already used for transcription | low: write a JSON sidecar | **best first step** if the transcriber uses Whisper |
| WhisperX (VAD + wav2vec2 forced alignment) | word | torch, wav2vec2 model, several hundred MB | medium | excellent accuracy, heavier install |
| stable-ts `align()` / `whisper-timestamped` alignment of known text | word | Whisper model | medium | good when the text is already correct |
| aeneas (DTW forced alignment with eSpeak) | word | `aeneas`, `espeak-ng`, ffmpeg | medium (pip) | mature offline aligner, works on existing text |
| Montreal Forced Aligner / Gentle (Kaldi) | phone/word | Kaldi install + models | high | accurate but awkward to ship portably |
| Vosk recognition, match recognised words to the text | word | language model per language | medium | useful offline, accuracy depends on the model |
| CTC forced alignment (`ctc-forced-aligner`, torchaudio MMS_FA) | word | torch + model | medium | modern, good accuracy |
| Cloud STT word timestamps (OpenAI `timestamp_granularities=["word"]`, AssemblyAI, Deepgram, Google) | word | API key, upload | low if already online | accurate and simple, but online and paid |

Things that decide the choice:

* **Where the text comes from.** If `new_txt_from_audio.py` already runs a
  model, ask it for word timestamps — that is nearly free and needs no
  second pass. The GUI already reads the standard JSON shapes, so the
  transcriber only has to drop `<audio>.json` next to the audio.
* **Existing, corrected text.** Word timestamps from recognition follow
  the *recognised* text, not your edited text. Forced alignment
  (WhisperX, aeneas, MFA, CTC) is the right tool when the `.txt` is
  authoritative.
* **Long files and memory.** Whisper-based aligners process audio in
  chunks and are much slower than playback; a 1-hour recording can take
  minutes. Manual marks remain useful for corrections even after an
  automatic pass.
* **Text fidelity.** Recognition output differs from edited text in
  punctuation, casing, filler words and repeats. The importer matches
  words sequentially and counts misses; a real forced aligner is more
  robust.
* **Segment-level is cheap.** SRT/VTT from most transcribers is enough for
  sentence highlighting (the highlight lasts until the next mark), which
  often reads well and needs no new dependency.

Suggested next step: make `new_txt_from_audio.py` write word timings when
its backend supports them, e.g.

```python
# faster-whisper
segments, info = model.transcribe(audio, word_timestamps=True)
timings = {
    "segments": [
        {"start": s.start, "text": s.text,
         "words": [{"word": w.word, "start": w.start, "end": w.end}
                   for w in s.words]}
        for s in segments
    ]
}
(audio_path.with_suffix(".json")).write_text(json.dumps(timings))
```

No GUI changes are needed: `_auto_import_timings()` picks the file up.
If the transcriber is not model based, aeneas is the lightest way to get
word times for an existing transcript; then use **Import timings...** on
its JSON output. Until either exists, **Auto-estimate** plus manual marks
is the practical path.

## Configuration summary

| Constant | Purpose |
| --- | --- |
| `TRANSCRIBER_SCRIPT` | Path of `new_txt_from_audio.py` |
| `TRANSCRIBER_ARGS` | Arguments passed to it (`{audio}`, `{folder}`, `{out}`) |
| `CTRL_CLICK_LEAD_SECONDS` | Reaction compensation for manual marks |
| `ESTIMATE_LEAD_SECONDS` / `ESTIMATE_TAIL_SECONDS` | Estimate margins |
| `AUDIO_EXTENSIONS` | Files shown in the list |
| `POLL_INTERVAL_MS` | Refresh rate of the clock and highlight |

Keyboard shortcuts: `Ctrl+O` open folder, `Ctrl+S` save text,
`Ctrl+Space` play/pause, `Ctrl+click` add sync mark,
`Ctrl+right-click` remove sync mark.

## Limitations

* Playback seeks by restarting the process on the subprocess backends, so
  seeking is a little slower than with pygame, and `afplay` cannot seek.
* pygame seeking inside long MP3 files decodes from the start, so a seek
  can take a moment and may be approximate.
* Without mutagen/ffprobe/pygame the duration can be unknown; the progress
  slider and **Auto-estimate** are then disabled, but manual marks and
  imported timings still work.
* Sync offsets are character positions: if the text is regenerated and
  re-wrapped, old offsets may point at the wrong words. Re-import/estimate
  in that case.
* The GUI assumes one text file per audio file, in the same folder, and
  does not recurse into subfolders.
