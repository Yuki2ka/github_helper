# Staged fix for `Yuki2ka/dev_helper`

The files in this folder are **copies** of the fixed versions from
[`Yuki2ka/dev_helper`](https://github.com/Yuki2ka/dev_helper), staged here only
because the Arena session for this task is attached to `github_helper` and its
diff can only show files inside this repository.

| File here | Source in `dev_helper` |
| --- | --- |
| `dev_helper/new_file/new_txt_from_audio_GUI.py` | `dev_helper/new_file/new_txt_from_audio_GUI.py` |
| `dev_helper/new_file/new_txt_from_audio_GUI-README.md` | `dev_helper/new_file/new_txt_from_audio_GUI-README.md` |
| `new_txt_from_audio_GUI.patch` | complete change set (also touches `new_txt_from_audio.py`, `TODO.txt` and adds `test_prompt/test_new_txt_from_audio_gui.py`) |

## Apply it to dev_helper

```bash
git clone https://github.com/Yuki2ka/dev_helper.git
cd dev_helper
git checkout 6444d7f
git apply /path/to/new_txt_from_audio_GUI.patch
python -m pytest -q test_prompt/test_new_txt_from_audio_gui.py
python dev_helper/new_file/new_txt_from_audio_GUI.py
```

The patch was verified to apply cleanly on `dev_helper@6444d7f`; the two
copied files are identical to the ones the patch produces.

Do not copy these copies back over the repository wholesale unless that is
intended: they are here for review and hand-off, and `github_helper` is a
different project.
