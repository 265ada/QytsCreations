# DirectInput / RawInput API-Hook Backend

This is the **"API Hook (Detours)"** backend the macro recorder calls
`detours`. It's the only backend that can feed input into games which
read **DirectInput** (`IDirectInputDevice8::GetDeviceState`) or poll
**Win32** (`GetAsyncKeyState` / `GetKeyState`) — i.e. the games that
ignore every other backend listed in the dropdown.

## How it works

```
   ┌────────────────────┐  named pipe   ┌──────────────────────────┐
   │ macro_recorder.py  │ ───────────▶  │ dinput_hook.dll          │
   │ DetoursBackend     │ \\.\pipe\... │ (injected into game.exe) │
   └────────────────────┘               │                          │
                                        │ Detours-hooks:           │
                                        │   GetAsyncKeyState       │
                                        │   GetKeyState            │
                                        │   IDirectInputDevice8::  │
                                        │      GetDeviceState      │
                                        │                          │
                                        │ Returns FAKE state to    │
                                        │ the game.                │
                                        └──────────────────────────┘
```

Nothing happens at the OS input level — the game asks "what keys are
pressed?" and our DLL answers with whatever the macro recorder told it,
overlaid on the real state. Your physical mouse and keyboard are never
touched.

## Prerequisites

1. **Visual Studio 2019/2022** with the "Desktop development with C++"
   workload.
2. **Microsoft Detours**:
   ```
   git clone https://github.com/microsoft/Detours C:\src\Detours
   cd C:\src\Detours
   nmake                         REM produces lib.x64\detours.lib etc.
   set DETOURS=C:\src\Detours
   ```
3. Your target process's **bitness**. A 64-bit game needs a 64-bit DLL;
   a 32-bit game needs a 32-bit DLL. Choose the matching VS command prompt
   before building.

## Build

```
cd <repo>\hooks\dinput_hook
build.bat
```

Output:
- `dinput_hook.dll` — the hook DLL
- `injector.exe`    — a minimal LoadLibrary-based injector

Build it once for `x64` (open the "x64 Native Tools Command Prompt for VS")
and, if you also need it for 32-bit games, again from the "x86 Native Tools
Command Prompt for VS" into a different folder.

## Inject

```
injector.exe <pid> C:\full\path\to\dinput_hook.dll
```

Where `<pid>` is the target game's process ID — you can read this from the
macro recorder's Pick Window dialog (PID column).

Once injected, you'll see a named pipe appear at
`\\.\pipe\macro_hook_<pid>`. The Macro Recorder's `DetoursBackend` connects
to it automatically the next time you play a macro with **Input Backend ->
API Hook (Detours)** selected on that macro.

You can confirm the pipe exists with PowerShell:

```powershell
Get-ChildItem \\.\pipe\ | Where-Object Name -like "macro_hook_*"
```

## Wire protocol

Same as the Serial HID backend — one ASCII line per command, `\n`-terminated:

| Command       | Effect inside the target process                       |
|---------------|--------------------------------------------------------|
| `KD <vk>`     | Set fake "down" state for VK code (Win32 + DirectInput)|
| `KU <vk>`     | Clear fake state                                       |
| `MA <x> <y>`  | Set absolute mouse position (drives DI delta)          |
| `MR <dx> <dy>`| Add relative mouse delta                               |
| `MD L\|R\|M`  | Press mouse button (Left/Right/Middle)                 |
| `MU L\|R\|M`  | Release mouse button                                   |
| `MW <dx> <dy>`| Wheel scroll (dy is vertical ticks)                    |
| `RESET`       | Clear all fake state                                   |

## Files

| File             | What it is                                            |
|------------------|-------------------------------------------------------|
| `dinput_hook.cpp`| Hook + pipe-server source                             |
| `injector.cpp`   | Minimal `LoadLibrary` injector                        |
| `build.bat`      | VS toolchain build script (needs `%DETOURS%` env var) |
| `README.md`      | This file                                             |

## Limitations & caveats

* **Vtable index for `GetDeviceState`** — the skeleton uses index 9 in
  `IDirectInputDevice8`. If your target uses a different DI build this may
  shift; the DLL prints nothing if the hook didn't take. Confirm with a
  debugger if needed.
* **DInput8 only** — for the older DInput7 path, hook the matching v-table
  on `IDirectInputDevice7`. The same technique applies.
* **Raw Input (`WM_INPUT`)** — games that consume Raw Input directly need an
  extra hook on `GetRawInputData` / `GetRawInputBuffer`. Not implemented in
  this skeleton; add similarly to `Hooked_GetAsyncKeyState`.
* **XInput / SDL** — controller-based games will need their own per-API
  hooks (`XInputGetState`, etc.). Same pattern.
* **Anti-cheat** — BattlEye, EAC, Vanguard, etc. *will* detect injected
  DLLs. Do not use this in protected multiplayer environments.

## Troubleshooting

| Symptom                                | Fix                                                       |
|----------------------------------------|-----------------------------------------------------------|
| `LoadLibraryA returned NULL`           | Bitness mismatch. Rebuild DLL for the target's bitness.   |
| Pipe doesn't appear                    | The DLL didn't load — check Process Hacker → Modules tab. |
| Game shows no fake input               | Game uses Raw Input or XInput — extend the hook.          |
| Macro recorder says "Hook pipe not found" | Inject the DLL first, then re-play the macro.          |
| Crash on game launch                   | Wrong DI8 vtable index, or game uses a custom DI wrapper. |
