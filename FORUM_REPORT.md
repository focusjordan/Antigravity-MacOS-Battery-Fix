# [Bug Report + Fix Script] Antigravity IDE `v2.6.0` macOS Extreme Battery Drain, Heat & Token Bloat — Causes, Scripted Fix & Upstream Suggestions

Hi Antigravity team & fellow Mac users,

After experiencing severe heat, rapid battery drain, and stalled USB-C charging on my Apple Silicon MacBook Air running **Antigravity IDE `v2.6.0`** (Electron `39.2.3` / VS Code `1.107.0`), I profiled the app down to the thread, battery-telemetry, and bundle level.

I found **four separate issues shipped in `v2.6.0`** (led by an infinite 100%-per-core CPU file-copy bug that **simply restarting the app does NOT permanently fix**), built a **safe, zero-tamper Python diagnostic & fix script** anyone can pull down and run in 10 seconds, and documented exact upstream fixes for the Google engineering team below.

- **GitHub Repo (Diagnostic & Automatic Fix Script):** https://github.com/focusjordan/Antigravity-MacOS-Battery-Fix

---

## 1. What Shipped in Antigravity IDE `v2.6.0` (Issues A, B, C & D)

### Issue A (Critical): Infinite 100%–300% CPU File-Copy Loop on macOS
- **What happens:** Antigravity's external `agy --hub` agent edits workspace files directly on disk while the IDE's built-in **Local History** (`workbench.localHistory.enabled: true` by default) automatically runs background `copyFile` backups of edited files into `~/Library/Application Support/Antigravity IDE/User/History/`.
- **The bug:** In `v2.6.0`'s bundled `Electron 39.2.3` (`libuv`), if an external agent edit shortens a file at the exact moment Local History is copying it, the background worker hits the new end-of-file early (`0` bytes read), misses its loop exit condition, and **spins that CPU core at 100% forever** (2 stuck copies = **200% nonstop CPU** and **~14 W** continuous heat).
- **Why closing windows or restarting alone doesn't fix it:** Closing a project window (`Cmd+W`) leaves those stuck threads spinning inside the main `Electron` process. And if you quit (`Cmd+Q`) and reopen *without* disarming the trigger, the exact same infinite loop happens again the next time the agent shortens a file mid-snapshot.

### Issue B: 25% of Background File Workers Blocked at Startup
- Electron's `libuv` pool defaults to **4** worker threads. At startup, `@vscode/policy-watcher` permanently blocks **1** of those 4 threads — leaving only 3 workers for all main-process file I/O (meaning 3 stuck copies from Issue A completely freeze the IDE).

### Issue C: Background GPU, 15-Second Health Polling & 8–16 GB RAM Swap Pressure
- Because Chromium window occlusion is disabled, UI animations in hidden background windows keep waking up the GPU helper and macOS `WindowServer`.
- Every open window pings `/healthz` every **15 seconds** (`antigravity.heartbeatIntervalMs: 15000`), spawns its own separate `200–900 MB` `agy --hub` process, leaves TypeScript Server capped at **3 GB**, and keeps unlimited background tabs open (`1.66 GB+` per renderer) — causing heavy RAM compression and SSD swap heat on 8–16 GB MacBooks.

### Issue D: Hidden AI Token & Quota Drain (Always-On Plugin Skills + Lockfile Searches)
- Every enabled plugin injects *all* of its skill descriptions into **every single chat step**. On my setup, having 6 common plugins enabled injected **73 skills (~8,600+ baseline input tokens on every step)** — with the **Science** plugin alone costing **~5,410 tokens per step** (over **100,000 tokens** across a 20-step coding task) even when doing ordinary web/app development!
- Additionally, unless `search.exclude` blocks lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`) and minified bundles (`*.min.js`), workspace searches can pull huge generated files into agent context.

---

## 2. Pull-Down Fix Script for Affected Mac Users (100% Safe / Zero App Modification)

I packaged all of the safe user-level fixes into `antigravity_battery_fix.py`. It **never modifies `/Applications/Antigravity IDE.app` or its Google code signature** (preserving logins, Keychain, chat history, and auto-updates), saves a `.bak` backup of your `settings.json` first, and applies:

- **Fix X:** Sets `"workbench.localHistory.enabled": false` (completely disarms the `libuv` infinite CPU loop while keeping Git & chat history untouched).
- **Fix Y:** Sets `UV_THREADPOOL_SIZE=8` via macOS `launchctl` so the background file-worker pool isn't bottlenecked.
- **Fix Z:** Slows `/healthz` polling by 75% (`15s` -> `60s`), turns on `workbench.reduceMotion`, caps clean background tabs at `15` (`excludeDirty: true` so unsaved files never close), and auto-scales TypeScript's RAM limit (`1.5 GB` on 8 GB Macs, `2 GB` on 16 GB Macs).
- **Fix W:** Adds lockfile & build-folder shields to `search.exclude` and `files.watcherExclude`, **plus prints a token-cost report of your installed plugins** so you can toggle off unused heavy packs live in **Customizations -> Plugins** (no restart required).

```bash
git clone https://github.com/focusjordan/Antigravity-MacOS-Battery-Fix.git
cd Antigravity-MacOS-Battery-Fix

python3 antigravity_battery_fix.py --diagnose   # Check live CPU, stuck handles, wattage & plugin tokens
python3 antigravity_battery_fix.py --apply      # Back up settings & apply all fixes
```
*(After running `--apply`, press **`Cmd+Q`** once and reopen Antigravity IDE to terminate any threads already wedged in RAM. Run `--restore` anytime to revert.)*

---

## 3. How to Verify & Measured Before/After Results

Run `python3 antigravity_battery_fix.py --diagnose` (or check macOS **Activity Monitor**) before and after applying:
1. Verify `Main IDE Process` (`Electron`) drops from `100%–300%` CPU to `< 1%` idle, with `0` stuck `User/History` file handles.
2. Open Antigravity's **Customizations -> Plugins** UI and toggle off any heavy skill packs you aren't actively using (plus use `/compact` on long threads).

**Measured on my fanless Apple M1 MacBook Air (8 GB RAM):**
- **Main `Electron` CPU:** `206.2%` continuous -> **`< 0.2%` idle** (`89.5%` system idle)
- **System Power Load (`AppleSmartBattery`):** `13.93 W` idle -> **`~2–4 W` idle**
- **Battery Sensor Temperature:** `40.6 °C` -> **`30.7 °C`**
- **Plugin Baseline Prompt Overhead:** `~8,600 tokens/step` -> **`~1,225 tokens/step`** (`~7,375 tokens` saved on every step after toggling off unused `Science`, `Firebase`, and `Android CLI` plugins)

---

## 4. Suggested Action Items for the Google Antigravity Engineering Team

To roll these fixes out natively for all Mac users in an upcoming update:

1. **Fix `uv__fs_copyfile` EOF spin (`Electron Framework` v39.2.3 `_uv__fs_post` at `0x286cdf0`):**
   - On macOS, `uv__fs_sendfile` (`0x286d96c`) fails `sendfile(2)` with `ENOTSOCK` (`0x26`) on regular files and falls back to `uv__fs_sendfile_emul` (`pread`). When an external `agy --hub` write truncates `srcfd` after `fstat(srcfd)` (`0x286d570`), `pread` returns `0` (`x22 = 0`). At `0x286d77c`, `tbnz x22, #0x3f` only breaks on `written < 0`, spinning forever when `written == 0` (and permanently leaking FDs + `DiskFileSystemProvider.doCloneFile` locks in `out/main.js`).
   - **Action:** Patch `uv__fs_copyfile` to break on `written == 0` / use APFS `fclonefileat`, or replace `Uk.copyFile` in `DiskFileSystemProvider.doCloneFile` (`out/main.js` `@828235`) with an EOF-safe user-space stream/chunked copy.
2. **Default `UV_THREADPOOL_SIZE=8` at main-process launch:** Compensates for `@vscode/policy-watcher` (`PolicyWatcher::Execute`) permanently parking 1 `libuv-worker` thread on a semaphore.
3. **Clean up orphaned `antigravity.handleAuthRefresh` call:** `out/vs/workbench/workbench.desktop.main.js` (`@27214227`) calls `executeCommand("antigravity.handleAuthRefresh")` on every hourly OAuth token refresh, but no handler is registered anywhere in `v2.6.0`.
4. **Smarter macOS power & plugin defaults:** Auto-cap `maxTsServerMemory` on 8 GB Macs, back off `/healthz` polling further on unfocused windows, and lazy-load large plugin skill packs (like the 48-skill `Science` bundle) so users aren't billed ~5,400 baseline prompt tokens per turn when working on unrelated codebases.
