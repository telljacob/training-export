# Training Export

A small Windows app that downloads **your own Garmin data** into a folder on your computer. Two parts, each one
on or off:

- **Sessions:** the original `.fit` file of every activity.
- **Health and recovery data:** sleep and its stages, breathing rate in sleep, bedtime, resting heart rate, nightly HRV, stress,
  Body Battery and weight. Each can be switched off. They go into one CSV per refresh, with the columns
  `Timestamp, Type, Value`, the shape of a TrainingPeaks metrics export.

You then drag the files into whatever training app you use. Training Export never sends your data anywhere except
from Garmin to your own folder.

## How it reads Garmin

It uses Garmin's official **Personal API** with **your own Personal API key**:

1. On Garmin Connect (web), open Personal API Key and choose Create. One year is the longest a key lasts.
2. Paste the key into Training Export. Windows keeps it in its own password store (Credential Manager), and it is sent
   only to Garmin. To stop all access, delete the key on Garmin Connect.

No Garmin password is ever typed into this app.

## The folder

```
Downloads\Training Export\        (or Desktop, or any folder you choose)
  New 2026-11-21 0712\        only what that refresh found: drag all of it into your training app
  All sessions\               every .fit so far
  export-state.json           what is already downloaded (ids and dates only)
  export log.txt              one line per refresh, counts only
```

- **The first export** reaches back 24 months by default. You can choose 6, 12, 24 or 36 months, or everything. You
  choose this once, at setup. Garmin limits how fast files can be fetched, so a long first export takes a while.
  Stopping it is fine: the next refresh carries on in the same "New" folder.
- **Every later refresh** fetches only what is new since the last one. Its health data reaches back 7 days past the
  last export, because Garmin re-scores recent nights.
- **A part switched on later** (sessions, or one kind of health data) catches up back to where the first export
  started.
- **A failed download** is tried again on the next refreshes, three times at most.

## Refreshing

- **When you press Refresh.** It can also refresh each time you open the window.
- **Automatically, at times you choose.** Up to 6 times a day, at least an hour apart. This makes one Windows
  scheduled task, "Training Export refresh", for your user only. It runs without a window while you're signed in,
  and a missed time runs when the computer wakes up. Choosing "When I press Refresh" removes the task.

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
- `training_export/settings.py`: your choices (`%APPDATA%\Training Export\settings.json`) and the key (Credential
  Manager).
- `training_export/schedule.py`: the scheduled task for automatic refreshes.
- `training_export/ui/index.html`: the window.

The .exe isn't code-signed yet, so Windows shows "Windows protected your PC" the first time. Choose More info, then
Run anyway.
