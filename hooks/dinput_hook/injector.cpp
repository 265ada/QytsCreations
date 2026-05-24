// ─────────────────────────────────────────────────────────────────────────
// injector.cpp — Minimal DLL injector / ejector for dinput_hook.dll
//
// Usage:
//   injector.exe <pid> <absolute_path_to_dll>              ← inject
//   injector.exe --eject <pid> <dll_basename>              ← eject
//
// Implementation: CreateRemoteThread + LoadLibraryA / FreeLibrary.
// Inject creates a remote LoadLibraryA call.  Eject enumerates the
// target's modules, finds the matching one, and invokes FreeLibrary in
// the target process to unload it (the DLL's DllMain DLL_PROCESS_DETACH
// runs, which detaches the Detours hooks and closes the pipe server).
// ─────────────────────────────────────────────────────────────────────────

#include <windows.h>
#include <tlhelp32.h>
#include <psapi.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#pragma comment(lib, "psapi.lib")

static int do_eject(DWORD pid, const wchar_t* dllBaseName) {
    HANDLE hProc = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
        PROCESS_VM_OPERATION  | PROCESS_VM_READ,
        FALSE, pid);
    if (!hProc) {
        wprintf(L"OpenProcess(%lu) failed: %lu\n", pid, GetLastError());
        return 2;
    }

    // Enumerate modules in the target process.  Try a few buffer sizes.
    HMODULE mods[2048];
    DWORD needed = 0;
    if (!EnumProcessModulesEx(hProc, mods, sizeof(mods), &needed,
                              LIST_MODULES_ALL)) {
        wprintf(L"EnumProcessModulesEx failed: %lu\n", GetLastError());
        CloseHandle(hProc);
        return 3;
    }
    DWORD count = needed / sizeof(HMODULE);
    HMODULE target = NULL;
    for (DWORD i = 0; i < count; ++i) {
        wchar_t name[MAX_PATH] = {0};
        if (GetModuleBaseNameW(hProc, mods[i], name, MAX_PATH)) {
            if (_wcsicmp(name, dllBaseName) == 0) {
                target = mods[i];
                break;
            }
        }
    }
    if (!target) {
        wprintf(L"Module %ls not loaded in PID %lu (already ejected?).\n",
                dllBaseName, pid);
        CloseHandle(hProc);
        return 4;
    }

    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    FARPROC freeLib = GetProcAddress(k32, "FreeLibrary");
    HANDLE hThread = CreateRemoteThread(
        hProc, NULL, 0,
        (LPTHREAD_START_ROUTINE)freeLib, (LPVOID)target, 0, NULL);
    if (!hThread) {
        wprintf(L"CreateRemoteThread(FreeLibrary) failed: %lu\n", GetLastError());
        CloseHandle(hProc);
        return 5;
    }
    WaitForSingleObject(hThread, INFINITE);
    DWORD result = 0;
    GetExitCodeThread(hThread, &result);
    CloseHandle(hThread);
    CloseHandle(hProc);
    wprintf(L"FreeLibrary returned %lu (non-zero = success)\n", result);
    return result ? 0 : 6;
}

int wmain(int argc, wchar_t* argv[]) {
    // Eject mode:  injector.exe --eject <pid> <dll_basename>
    if (argc == 4 && _wcsicmp(argv[1], L"--eject") == 0) {
        DWORD pid = (DWORD)_wtoi(argv[2]);
        return do_eject(pid, argv[3]);
    }
    if (argc != 3) {
        wprintf(L"Inject:  %ls <pid> <full_dll_path>\n", argv[0]);
        wprintf(L"Eject:   %ls --eject <pid> <dll_basename>\n", argv[0]);
        return 1;
    }

    DWORD pid = (DWORD)_wtoi(argv[1]);
    const wchar_t* dll = argv[2];

    // Convert dll path to ANSI for LoadLibraryA
    char dll_a[MAX_PATH];
    WideCharToMultiByte(CP_ACP, 0, dll, -1, dll_a, MAX_PATH, NULL, NULL);

    HANDLE hProc = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
        PROCESS_VM_OPERATION  | PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE, pid);
    if (!hProc) {
        wprintf(L"OpenProcess(%lu) failed: %lu\n", pid, GetLastError());
        return 2;
    }

    SIZE_T sz = strlen(dll_a) + 1;
    LPVOID remote = VirtualAllocEx(hProc, NULL, sz, MEM_COMMIT, PAGE_READWRITE);
    if (!remote) {
        wprintf(L"VirtualAllocEx failed: %lu\n", GetLastError());
        CloseHandle(hProc); return 3;
    }

    if (!WriteProcessMemory(hProc, remote, dll_a, sz, NULL)) {
        wprintf(L"WriteProcessMemory failed: %lu\n", GetLastError());
        VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
        CloseHandle(hProc); return 4;
    }

    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    FARPROC loadLib = GetProcAddress(k32, "LoadLibraryA");

    HANDLE hThread = CreateRemoteThread(
        hProc, NULL, 0,
        (LPTHREAD_START_ROUTINE)loadLib, remote, 0, NULL);
    if (!hThread) {
        wprintf(L"CreateRemoteThread failed: %lu\n", GetLastError());
        VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
        CloseHandle(hProc); return 5;
    }

    WaitForSingleObject(hThread, INFINITE);
    DWORD result = 0;
    GetExitCodeThread(hThread, &result);
    CloseHandle(hThread);
    VirtualFreeEx(hProc, remote, 0, MEM_RELEASE);
    CloseHandle(hProc);

    if (result == 0) {
        wprintf(L"LoadLibraryA returned NULL — injection failed.\n");
        return 6;
    }
    wprintf(L"Injected.  Module base in target = 0x%p\n", (void*)(uintptr_t)result);
    return 0;
}
