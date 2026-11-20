# Training Export

A small app for Windows and Mac that downloads **your own Garmin data** into a folder on your computer. Two parts,
each one on or off:

- **Sessions:** the original `.fit` file of every activity.
- **Health and recovery data:** sleep and its stages, breathing rate in sleep, bedtime, resting heart rate, nightly HRV, stress,
  Body Battery and weight. Each can be switched off. They go into one CSV per refresh, with the columns
  `Timestamp, Type, Value`, the shape of a TrainingPeaks metrics export.

You then drag the whole folder onto Brick by Brick's Upload page, which skips anything it already has. Training
Export never sends your data anywhere except from Garmin to your own folder.

## Download

- **Windows:** [Training-Export-Windows.exe](../../releases/latest/download/Training-Export-Windows.exe). The app
  isn't code-signed yet, so the first time Windows says "Windows protected your PC": choose **More info**, then
  **Run anyway**.
- **Mac** (Apple silicon, M1 or newer): [Training-Export-Mac.zip](../../releases/latest/download/Training-Export-Mac.zip).
  Open the zip and drag Training Export into Applications. The app isn't notarized by Apple yet, so the first open
  is refused: open **System Settings → Privacy & Security**, scroll down to "Training Export was blocked" and choose
  **Open Anyway**. After that it opens normally.

## How it reads Garmin

It uses Garmin's official **Personal API** with **your own Personal API key**:

1. On Garmin Connect (web), open Personal API Key and choose Create. One year is the longest a key lasts.
2. Paste the key into Training Export. Your computer keeps it in its own password store (Credential Manager on
   Windows, the Keychain on a Mac), and it is sent only to Garmin. To stop all access, delete the key on Garmin Connect.

No Garmin password is ever typed into this app.

## The folder

```
Downloads\Training Export\        (or Desktop, or any folder you choose): drag all of it onto Upload
  Activities\                 every session's .fit, e.g. "2026-11-21 0712 running 12345678901.fit"
  Health\                     one CSV per refresh, e.g. "health-2026-11-14-2026-11-21 0712.csv"
  export-state.json           what is already downloaded (ids and dates only)
  export log.txt              one line per refresh, counts only
```

- **Names never change.** An activity's file keeps its name, so a re-dropped folder is skipped by name. Each
  health file is written once, and its name carries its date window and the refresh's time, so every refresh's file
  is new to Upload. Where two files cover the same day, the one whose window ends later wins.
- **Older versions' folders** ("All activities" or "All sessions", and the dated "New ..." folders) are folded into
  Activities and Health on the next refresh. Duplicates go; a file that differs is left where it was, never
  overwritten.

- **The first export** reaches back 24 months by default. You can choose 6, 12, 24 or 36 months, or everything. You
  choose this once, at setup. Garmin limits how fast files can be fetched, so a long first export takes a while.
  Stopping it is fine: the next refresh carries on where it stopped.
- **Every later refresh** fetches only what is new since the last one. Its health data reaches back 7 days past the
  last export, because Garmin re-scores recent nights.
- **A part switched on later** (sessions, or one kind of health data) catches up back to where the first export
  started.
- **A failed download** is tried again on the next refreshes, three times at most.

## Refreshing

- **When you press Refresh.** It can also refresh each time you open the window.
- **Automatically, at times you choose.** Up to 6 times a day, at least an hour apart. On Windows this makes one
  scheduled task, "Training Export refresh"; on a Mac one launch agent, `local.training-export.refresh` in
  `~/Library/LaunchAgents`. Either is for your user only, runs without a window while you're signed in, and a missed
  time runs when the computer wakes up. Choosing "When I press Refresh" removes it. Opening the app updates it, so
  moving the app to another folder is fine.

## For developers

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q          # fake Garmin replies only; no key, no network
.venv\Scripts\python run.py                # the window
.venv\Scripts\python run.py --refresh      # one refresh without a window
powershell -ExecutionPolicy Bypass -File build.ps1   # dist\Training Export.exe
```

- `training_export/garmin.py`: the Personal API client. The key goes only to the token call, and the token is held
  in memory. Neither ever appears in output.
- `training_export/health.py`: Garmin's health replies turned into `Timestamp, Type, Value` rows.
- `training_export/exporter.py`: one refresh, covering the folders, the state, stop and resume, and retries.
- `training_export/settings.py`: your choices (`%APPDATA%\Training Export\settings.json`, on a Mac
  `~/Library/Application Support/Training Export/settings.json`) and the key (Credential Manager or Keychain).
- `training_export/schedule.py`: the scheduled task (Windows) or launch agent (Mac) for automatic refreshes.
- `training_export/ui/index.html`: the window.

**Releases:** `.github/workflows/release.yml` builds both downloads on GitHub's Windows and Mac machines, after the
tests pass on each. Push a version tag (`git tag v1.1.0`, `git push origin v1.1.0`) and it publishes a release whose
file names never change, so the download links above always give the newest. Run the workflow by hand for a trial
build without publishing.
