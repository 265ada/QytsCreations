# QytCroRec — GitHub setup & auto-update

This guide covers two related goals:

1. **Push the project to GitHub** so the code is versioned and others can use it.
2. **Enable in-app auto-update** so every running copy can pull new releases automatically.

---

## 1.  One-time: build the .exe

```cmd
build_exe.bat
```

The first time this runs it will `pip install pyinstaller`. Output:

```
dist\QytCroRec.exe          (~40–50 MB — Python + PyQt6 + pynput + hook DLLs all inside)
```

This single .exe runs on any Windows 10/11 machine without Python installed.

---

## 2.  Push to GitHub

### 2.1  Create the repo

1. On github.com, **New repository**.
   - Name: `qytcrorec` (or whatever)
   - Public if you want auto-update to work without authentication.
2. Locally:

```cmd
cd C:\Users\prkid\Documents\macro_recorder
git init
git branch -M main
git add QytCroRec.py QytCroRec.bat build_exe.bat hooks GITHUB_SETUP.md hid_firmware
git commit -m "Initial commit"
git remote add origin https://github.com/<YOUR_USER>/qytcrorec.git
git push -u origin main
```

### 2.2  Decide what NOT to commit

Create `.gitignore`:

```
__pycache__/
*.pyc
build/
dist/
*.spec
*.obj
*.exp
*.lib
hooks/dinput_hook/*.dll
hooks/dinput_hook/*.exe
.macro_recorder/
```

You can either:

- **Commit the built DLLs/EXE** (so end users don't need a compiler) — drop the relevant lines from `.gitignore`.
- **Or distribute them via Releases** (recommended — see below).

---

## 3.  Set up auto-update

Auto-update is already coded in `QytCroRec.py` — you just need to host two files at known URLs:

### 3.1  Point QytCroRec at YOUR URLs

Edit the top of `QytCroRec.py`:

```python
UPDATE_VERSION_URL = "https://raw.githubusercontent.com/<YOUR_USER>/qytcrorec/main/version.json"
UPDATE_SCRIPT_URL  = "https://raw.githubusercontent.com/<YOUR_USER>/qytcrorec/main/QytCroRec.py"
AUTO_UPDATE_ENABLED = True
```

Commit + push that change **first**, so every existing copy already knows where to look.

### 3.2  Create `version.json` in the repo root

```json
{
  "version": "1.17",
  "notes": "Initial public release.  Hook DLL is bundled in the .exe."
}
```

Commit + push it.

### 3.3  How updating works (no further config needed)

Every time `QytCroRec` starts, a background thread:

1. Fetches `UPDATE_VERSION_URL`
2. Compares `version` to its own `__version__`
3. If remote is newer, prompts the user: "Update & Restart"
4. Downloads `UPDATE_SCRIPT_URL`, sanity-checks it (>1KB and contains the string "QytCroRec"), replaces the local file, keeps the previous as `.prev.py`, and relaunches.

### 3.4  Releasing a new version

When you fix something:

1. Bump `__version__` in `QytCroRec.py` (e.g. `"1.18"`).
2. Bump `version` in `version.json` to the same value.
3. Update `notes` with a short changelog.
4. Commit + push both files.

```cmd
git add QytCroRec.py version.json
git commit -m "v1.18: <one-line summary>"
git tag v1.18
git push --tags origin main
```

Every running copy will see the update at next startup.

---

## 4.  Distributing the .exe via GitHub Releases (recommended)

If you want non-developer users to just download a working .exe:

1. Build it locally:  `build_exe.bat`
2. On GitHub:  Releases → **Draft a new release**.
3. Tag = `v1.18` (matches your `__version__`).
4. Upload `dist\QytCroRec.exe`.
5. Also upload the hook artifacts in case advanced users want them:
   - `hooks/dinput_hook/dinput_hook_x64.dll`
   - `hooks/dinput_hook/dinput_hook_x86.dll`
   - `hooks/dinput_hook/injector_x64.exe`
   - `hooks/dinput_hook/injector_x86.exe`
6. Publish.

Users download `QytCroRec.exe`, run it. No prerequisites needed.

### 4.1  Auto-update for .exe distribution

The current auto-updater downloads the .py source.  Two options for .exe users:

**Option A — Source updates only (simplest, current behavior):**
.exe users have to manually download a new .exe from your Releases page when there's an update. The in-app updater will tell them an update is available but the download won't work because it expects .py.

**Option B — Updater that downloads .exe replacements:**
Modify `UPDATE_SCRIPT_URL` to point at the latest .exe release asset URL pattern, e.g.:
```python
UPDATE_SCRIPT_URL = (
    "https://github.com/<YOUR_USER>/qytcrorec/releases/latest/download/QytCroRec.exe"
)
```
And update `AutoUpdater.download_and_install` to swap the running .exe (this needs a small helper batch — Windows can't replace a running .exe, so you write to `.new.exe`, schedule a swap on next launch).

For now, Option A is simpler.  Tell users "when the app says an update is available, go grab the new .exe from Releases".

---

## 5.  Update workflow cheat sheet

```
# After making code changes:
notepad QytCroRec.py              # bump __version__
notepad version.json              # bump version + notes
build_exe.bat                     # produce dist\QytCroRec.exe

git add -A
git commit -m "v1.18: my change"
git tag v1.18
git push --tags origin main

# Create GitHub Release v1.18, upload dist\QytCroRec.exe
```

That's it.  Every QytCroRec running anywhere will see the new version next launch.
