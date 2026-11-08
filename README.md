# Antigravity IDE macOS Battery, Heat & Token-Efficiency Fix (`v2.6.0+`)

If **Google Antigravity IDE** is making your MacBook run hot, draining your battery in a couple of hours, or failing to charge properly while plugged in — **it isn't just "AI being heavy," and simply restarting the app won't fix it.**

We investigated Antigravity IDE `v2.6.0` on Apple Silicon macOS and uncovered **four distinct bugs and resource-drain issues** shipped in the current build (including an infinite file-copy CPU loop and hidden plugin prompt bloat that burns thousands of AI tokens on every chat message).

This repository provides a **single, safe Python script** (`antigravity_battery_fix.py`) that diagnoses your Mac and applies all of the safe configuration fixes automatically — **without modifying `/Applications/Antigravity IDE.app`, touching its Apple code signature, or risking your chat history or sign-ins.**

---

## Quick Start (Pull Down & Run)

Open **Terminal** on your Mac and run:

```bash
git clone https://github.com/focusjordan/Antigravity-MacOS-Battery-Fix.git
cd Antigravity-MacOS-Battery-Fix

# 1. Optional: Run a read-only check first to see if your Mac currently has stuck CPU threads or token-heavy plugins
python3 antigravity_battery_fix.py --diagnose

# 2. Back up your current settings and apply all safe fixes automatically
python3 antigravity_battery_fix.py --apply
```

**Final step after running `--apply`:** Press **`Cmd+Q`** once to completely quit Antigravity IDE and reopen it. *(All of your open tabs, workspaces, chat history, and logins will restore automatically.)*

To undo every change at any point in the future (for example, once Google ships an official patch):
```bash
python3 antigravity_battery_fix.py --restore
```

*(Note: A `pyproject.toml` is also included if you prefer installing it as a CLI command via `pipx install .` -> `antigravity-battery-fix --apply`.)*

---

## What Shipped Broken in Antigravity IDE `v2.6.0`?

### Issue A: Infinite 100%–300% CPU File-Copy Loop (The Primary Heat & Battery Bug)
Antigravity IDE `v2.6.0` bundles Electron `39.2.3`, whose macOS file-copy engine (`libuv`) has a bug when a file shrinks mid-copy. Meanwhile, Antigravity's AI agent (`agy --hub`) edits files directly on disk at the exact same moment the IDE's built-in **"Local History"** feature automatically tries to save backup copies of those files.
- Whenever an AI edit makes a file even 1 byte shorter during that split-second backup, a background worker misses the end-of-file stop condition and **spins a CPU core at 100% forever**.
- Closing your project window (`Cmd+W`) does **not** stop it — the main app process keeps burning 100%–300% CPU (~14 Watts of heat on a fanless MacBook Air) in the background until you fully quit (`Cmd+Q`).
- **Why restarting alone is NOT a fix:** Without disabling that background file-copy collision, quitting and reopening the app only resets the clock until the very next time the AI agent shortens a file.

### Issue B: Shrunk Background File-Worker Pool at Startup
Node/Electron starts with **4** background file-worker threads by default. At launch, Antigravity's internal policy watcher permanently occupies **1** of those 4 threads — cutting available file capacity by 25% immediately, and meaning just 3 stuck file copies from Issue A will freeze file operations across the entire IDE.

### Issue C: Background Window GPU, Health-Check Polling & 8–16 GB RAM Swap Churn
- Antigravity disables macOS's hidden-window sleep optimization, so animated spinners in background IDE windows keep waking up the GPU and macOS `WindowServer` at 60 FPS.
- Every open IDE window pings its local `/healthz` server every **15 seconds** (240 wakeups/hour per window) and runs its own separate `agy --hub` backend server (`200–900 MB` RAM each).
- TypeScript's language server defaults to a **3 GB** memory cap and unlimited background tabs stay in memory (`1.66 GB+` per window), pushing 8 GB and 16 GB MacBooks into heavy RAM compression and SSD swap heat.

### Issue D: Hidden AI Token & Quota Drain (Plugins + Unfiltered Search)
1. **Always-On Plugin Skills:** Every enabled plugin injects *all* of its skill descriptions into **every single chat step** you send. In our audit, 6 installed plugins injected **73 skills (~8,600+ input tokens on every step)** — with the **Science** plugin alone adding **48 skills (~5,410 tokens per step, or >100,000 tokens across a 20-step coding task)** even while doing ordinary web/app work!
2. **Lockfiles in Search:** Without explicit search exclusions, workspace searches can pull massive lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`) and minified build files (`*.min.js`) into agent tool outputs, wasting thousands of tokens and slowing responses.

---

## What `antigravity_battery_fix.py --apply` Fixes Automatically

The script saves an untouched backup of your `~/Library/Application Support/Antigravity IDE/User/settings.json` first, leaves `/Applications/Antigravity IDE.app` 100% untouched, and applies five safe fixes:

1. **Fix X — Disarms the Infinite CPU Loop (`workbench.localHistory.enabled: false`):** Turns off redundant duplicate file copies into `User/History/` (Git history, chat sessions, and existing files are completely untouched), preventing the buggy file-copy path from ever running in the background.
2. **Fix Y — Doubles Background File Workers (`UV_THREADPOOL_SIZE=8`):** Sets up a lightweight macOS `launchctl` Login Item so Antigravity gets **8** background file workers instead of **4**.
3. **Fix Z — Cuts Idle GPU, Polling & RAM Swap:**
   - Slows `/healthz` polling from `15s` -> `60s` (**75% fewer idle wakeups**).
   - Enables `workbench.reduceMotion: "on"` so hidden windows stop driving 60 FPS GPU animations.
   - Auto-scales TypeScript's memory cap to your Mac's RAM (`1536 MB` on 8 GB Macs, `2048 MB` on 16 GB Macs).
   - Caps old *saved* background tabs at `15` while setting `excludeDirty: true` (**unsaved files are never closed**).
4. **Fix W — Protects AI Token Quota (`search.exclude` & `files.watcherExclude`):** Blocks lockfiles, `node_modules`, and minified build folders (`dist`, `build`, `.next`, `coverage`, `*.min.js`) from both file watchers and AI searches.
5. **Bonus — Built-In Plugin Token Audit:** Prints a ranked breakdown of your installed plugins in `~/.gemini/config/plugins/` showing how many baseline tokens per chat step each plugin costs you, so you know which unused packs to toggle off in Antigravity's **Customizations / Plugins** UI (takes effect live with zero restart; also use `/compact` on long chats to avoid re-sending huge history on every turn).

---

## How to Verify the Fixes (Before & After)

1. Run `python3 antigravity_battery_fix.py --diagnose` **before** applying to check your current `Main IDE Process` CPU%, stuck `Local History` handles, live Mac wattage, and plugin token overhead.
2. Run `python3 antigravity_battery_fix.py --apply`, then quit Antigravity IDE once (**`Cmd+Q`**) and reopen it.
3. Run `python3 antigravity_battery_fix.py --diagnose` again (or check macOS **Activity Monitor -> Energy / CPU**):
   - `Main IDE Process` (`Electron`) should sit below **`0.5%–2%` CPU** when idle (instead of `100%–300%`).
   - Stuck `Local History` file handles will read **`0`**.
   - App code signature check will read **`Intact`**.

### Measured Results (Apple M1 MacBook Air, 8 GB RAM, Fanless)

| Metric | Before (`v2.6.0` Default, 2 Stuck Threads) | After Running Script + Toggling Unused Plugins |
| :--- | :--- | :--- |
| **Main IDE Process CPU** | **190% – 206% nonstop** (for 6+ days) | **`< 0.2%` idle** (`89.5%` whole-Mac idle) |
| **Total Mac Power Draw** | **13.93 Watts** while completely idle | **~2 – 4 Watts** idle (`~6.9 W` active on battery) |
| **Battery / Chassis Temp** | **40.6 °C** (hot chassis, stalled charging) | **30.7 °C** battery sensor (**~10 °C cooler**) |
| **Baseline AI Plugin Tokens** | **~8,600+ tokens** added to *every* step | **~1,225 tokens/step** (**~7,375 tokens saved per step**) |

---

## Suggested Upstream Fixes for the Google Antigravity Team

To roll these improvements out natively for all Mac users in an upcoming Antigravity IDE release (`v2.6.1+`):

1. **Fix or bypass `uv__fs_copyfile` on macOS:** Ensure Electron's bundled `libuv` breaks out of `uv__fs_copyfile` when `uv__fs_sendfile` returns `0` bytes at EOF (or use `fclonefileat` / a user-space EOF-safe copy inside `DiskFileSystemProvider.doCloneFile` in `out/main.js`) so external `agy --hub` file truncations can never trigger an infinite loop.
2. **Raise default `UV_THREADPOOL_SIZE` (e.g. `8`) at main-process startup** to compensate for `@vscode/policy-watcher` permanently blocking 1 worker thread.
3. **Tune macOS laptop defaults:** Auto-scale `typescript.tsserver.maxTsServerMemory` on 8 GB Macs, relax `antigravity.heartbeatIntervalMs` when windows are unfocused/hidden, and remove or guard the orphaned hourly `executeCommand("antigravity.handleAuthRefresh")` call in `workbench.desktop.main.js`.
4. **On-demand / lazy plugin skill loading:** Display per-plugin token cost in the Customizations UI or load large plugin skill catalogs (such as the 48-skill `Science` pack) lazily by keyword rather than injecting ~5,400 tokens into every unrelated coding turn.

<details>
<summary><strong>Low-Level Engineering Proof (ARM64 Disassembly & Offsets for Google Engineers)</strong></summary>

See [`docs/TECHNICAL_INVESTIGATION.md`](docs/TECHNICAL_INVESTIGATION.md) for the full `sample` thread trace, `lsof` file-descriptor proof, and ARM64 disassembly of `_uv__fs_post` (`0x286cdf0` / `0x286d77c` `tbnz x22, #0x3f` missing `written == 0` check) inside `Electron Framework` (`v39.2.3`).

</details>

---

## Running Unit Tests

```bash
python3 -m unittest discover -s tests -v
```
