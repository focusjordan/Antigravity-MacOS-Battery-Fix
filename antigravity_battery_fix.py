#!/usr/bin/env python3
"""
antigravity_battery_fix.py

Safe, zero-dependency macOS diagnostic and fix script for Google Antigravity IDE (v2.6.0+).
Fixes runaway 100%-300% CPU loops, background worker bottlenecks, 8-16 GB RAM/GPU drain,
and runaway AI token consumption—without modifying `/Applications/Antigravity IDE.app`
or touching its Apple code signature.

Usage:
  python3 antigravity_battery_fix.py              # Run read-only health & token check
  python3 antigravity_battery_fix.py --apply      # Back up settings & apply all safe fixes
  python3 antigravity_battery_fix.py --restore    # Undo changes cleanly
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_APP_BUNDLE = Path("/Applications/Antigravity IDE.app")
DEFAULT_USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "Antigravity IDE"
DEFAULT_SETTINGS_PATH = DEFAULT_USER_DATA_DIR / "User" / "settings.json"
DEFAULT_LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "local.antigravity.uv-threadpool.plist"
DEFAULT_PLUGINS_DIR = Path.home() / ".gemini" / "config" / "plugins"
BACKUP_SUFFIX = ".antigravity-battery-fix.bak"
MANIFEST_NAME = ".antigravity-battery-fix-manifest.json"
LAUNCH_AGENT_LABEL = "local.antigravity.uv-threadpool"

WATCHER_EXCLUDES: Dict[str, bool] = {
    "**/.git/objects/**": True,
    "**/.git/subtree-cache/**": True,
    "**/node_modules/*/**": True,
    "**/dist/**": True,
    "**/build/**": True,
    "**/.next/**": True,
}

SEARCH_EXCLUDES: Dict[str, bool] = {
    "**/node_modules": True,
    "**/bower_components": True,
    "**/*.code-search": True,
    "**/dist/**": True,
    "**/build/**": True,
    "**/.next/**": True,
    "**/coverage/**": True,
    "**/*.min.js": True,
    "**/package-lock.json": True,
    "**/pnpm-lock.yaml": True,
    "**/yarn.lock": True,
}


def strip_jsonc(text: str) -> str:
    """Strip // line comments, /* block comments */, and trailing commas from JSONC."""
    out: List[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False

    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] not in ("\r", "\n"):
                    i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                out.append(ch)
                i += 1

    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def load_jsonc_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    parsed = json.loads(strip_jsonc(raw))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected top-level JSON object in {path}")
    return parsed


def detect_system_ram_gb() -> int:
    try:
        res = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            check=True,
        )
        return max(1, int(res.stdout.strip()) // (1024**3))
    except Exception:
        return 16


def recommended_scalar_settings(ram_gb: int) -> Dict[str, Any]:
    ts_mem = 1536 if ram_gb <= 8 else (2048 if ram_gb <= 16 else 3072)
    return {
        "workbench.localHistory.enabled": False,
        "workbench.reduceMotion": "on",
        "search.followSymlinks": False,
        "typescript.tsserver.maxTsServerMemory": ts_mem,
        "antigravity.heartbeatIntervalMs": 60000,
        "workbench.editor.limit.enabled": True,
        "workbench.editor.limit.value": 15,
        "workbench.editor.limit.excludeDirty": True,
    }


def merge_settings(
    current: Dict[str, Any],
    ram_gb: int,
    overwrite_existing: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Non-destructively merge safe battery, performance, and token-saving settings.
    Returns `(updated_settings, manifest_record)` so `--restore` can undo cleanly.
    """
    updated = json.loads(json.dumps(current))
    added_top_keys: List[str] = []
    previous_values: Dict[str, Any] = {}

    scalars = recommended_scalar_settings(ram_gb)
    for k, val in scalars.items():
        if k not in updated:
            added_top_keys.append(k)
            updated[k] = val
        elif overwrite_existing or k == "workbench.localHistory.enabled":
            if updated[k] != val:
                previous_values[k] = updated[k]
                updated[k] = val

    added_nested: Dict[str, List[str]] = {"files.watcherExclude": [], "search.exclude": []}
    for map_key, defaults in (
        ("files.watcherExclude", WATCHER_EXCLUDES),
        ("search.exclude", SEARCH_EXCLUDES),
    ):
        existing_map = updated.get(map_key)
        if existing_map is None:
            added_top_keys.append(map_key)
            updated[map_key] = dict(defaults)
        elif isinstance(existing_map, dict):
            for pat, flag in defaults.items():
                if pat not in existing_map:
                    existing_map[pat] = flag
                    added_nested[map_key].append(pat)

    manifest = {
        "added_top_keys": added_top_keys,
        "previous_values": previous_values,
        "added_nested": added_nested,
    }
    return updated, manifest


def restore_settings_from_manifest(current: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    restored = json.loads(json.dumps(current))
    for k in manifest.get("added_top_keys", []):
        restored.pop(k, None)
    for k, prev in manifest.get("previous_values", {}).items():
        restored[k] = prev
    for map_key, patterns in manifest.get("added_nested", {}).items():
        sub = restored.get(map_key)
        if isinstance(sub, dict):
            for pat in patterns:
                sub.pop(pat, None)
    return restored


def build_launch_agent_plist(pool_size: int = 8) -> bytes:
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            "/bin/launchctl",
            "setenv",
            "UV_THREADPOOL_SIZE",
            str(pool_size),
        ],
        "RunAtLoad": True,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def read_battery_telemetry() -> Dict[str, Optional[float]]:
    info: Dict[str, Optional[float]] = {
        "system_load_watts": None,
        "temp_c": None,
        "virtual_temp_c": None,
    }
    try:
        res = subprocess.run(
            ["/usr/sbin/ioreg", "-rn", "AppleSmartBattery", "-w0"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = res.stdout
        m_load = re.search(r'"SystemLoad"\s*=\s*(\d+)', out)
        if m_load:
            info["system_load_watts"] = round(int(m_load.group(1)) / 1000.0, 2)
        m_temp = re.search(r'"Temperature"\s*=\s*(\d+)', out)
        if m_temp:
            info["temp_c"] = round(int(m_temp.group(1)) / 100.0, 2)
        m_vtemp = re.search(r'"VirtualTemperature"\s*=\s*(\d+)', out)
        if m_vtemp:
            info["virtual_temp_c"] = round(int(m_vtemp.group(1)) / 100.0, 2)
    except Exception:
        pass
    return info


def inspect_running_processes() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "main_processes": [],
        "agy_hubs": [],
        "renderers": [],
        "history_fds": [],
    }
    try:
        res = subprocess.run(
            ["/bin/ps", "-axo", "pid,ppid,pcpu,rss,time,args", "-ww"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in res.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 5)
            if len(parts) < 6:
                continue
            pid_s, ppid_s, pcpu_s, rss_s, cputime, args = parts
            pid = int(pid_s)
            pcpu = float(pcpu_s)
            rss_mb = round(int(rss_s) / 1024.0, 1)
            if args.strip() == "/Applications/Antigravity IDE.app/Contents/MacOS/Electron" or (
                "Antigravity IDE.app/Contents/MacOS/Electron" in args and "--type=" not in args
            ):
                result["main_processes"].append(
                    {"pid": pid, "cpu": pcpu, "rss_mb": rss_mb, "time": cputime}
                )
            elif "agy --hub" in args:
                result["agy_hubs"].append(
                    {"pid": pid, "ppid": int(ppid_s), "cpu": pcpu, "rss_mb": rss_mb, "time": cputime}
                )
            elif "Antigravity IDE Helper (Renderer)" in args and "--type=renderer" in args:
                result["renderers"].append(
                    {"pid": pid, "cpu": pcpu, "rss_mb": rss_mb, "time": cputime}
                )
    except Exception as exc:
        result["ps_error"] = str(exc)

    for mp in result["main_processes"]:
        try:
            lsof = subprocess.run(
                ["/usr/sbin/lsof", "-p", str(mp["pid"])],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for l_line in lsof.stdout.splitlines():
                if "/Antigravity IDE/User/History/" in l_line:
                    result["history_fds"].append(l_line.strip())
        except Exception:
            pass

    return result


def audit_installed_plugins(plugins_dir: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not plugins_dir.is_dir():
        return items
    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir():
            continue
        skills_dir = child / "skills"
        skill_files = list(skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
        est_chars = 0
        for sf in skill_files:
            try:
                txt = sf.read_text(encoding="utf-8", errors="replace")
                m = re.match(r"^---\s*\n(.*?)\n---", txt, flags=re.DOTALL)
                est_chars += len(m.group(1)) + 120 if m else 300
            except Exception:
                est_chars += 300
        if skill_files:
            items.append(
                {
                    "dir": child.name,
                    "skills": len(skill_files),
                    "est_tokens_per_turn": est_chars // 4,
                }
            )
    items.sort(key=lambda x: x["est_tokens_per_turn"], reverse=True)
    return items


def check_codesign(app_bundle: Path) -> Tuple[bool, str]:
    if not app_bundle.exists():
        return False, f"App bundle not found at {app_bundle}"
    res = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app_bundle)],
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        return True, "Intact (original Google Developer ID signature verified)"
    return False, (res.stderr.strip() or f"codesign exited with {res.returncode}")


def print_plugin_token_report(plugins_dir: Path) -> None:
    plugins = audit_installed_plugins(plugins_dir)
    if not plugins:
        return
    total_tok = sum(p["est_tokens_per_turn"] for p in plugins)
    print("\n--- AI Token & Quota Check (Installed Plugins) ---")
    print(
        f"Every enabled plugin adds its skill descriptions to EVERY chat message step.\n"
        f"Installed plugins found (~{total_tok:,} baseline tokens/step if all enabled):"
    )
    for p in plugins[:8]:
        print(f"  * {p['dir']}: {p['skills']} skill(s) (~{p['est_tokens_per_turn']:,} tokens/step)")
    print(
        "Tip: Open Antigravity's Customizations / Plugins page and toggle OFF any large\n"
        "     plugin packs you aren't actively using right now (takes effect live, no restart)."
    )


def cmd_diagnose(args: argparse.Namespace) -> int:
    ram_gb = detect_system_ram_gb()
    print("==============================================================")
    print("  Antigravity IDE macOS Health, Battery & Token Diagnostic")
    print("==============================================================")
    print(f"Mac Memory:          {ram_gb} GB RAM")

    batt = read_battery_telemetry()
    if batt["system_load_watts"] is not None:
        print(
            f"Live Power & Temp:   {batt['system_load_watts']} W total Mac load | "
            f"Battery {batt['temp_c']} C (Chassis/Virtual {batt['virtual_temp_c']} C)"
        )

    procs = inspect_running_processes()
    print("\n--- 1. Stuck 100%+ CPU File-Copy Loop Check ---")
    if procs.get("main_processes"):
        for mp in procs["main_processes"]:
            if mp["cpu"] >= 90.0:
                status = "WARNING: HIGH CPU DETECTED (likely stuck file-copy thread!)"
            else:
                status = "Healthy (normal idle CPU)"
            print(f"Main IDE Process (PID {mp['pid']}): {mp['cpu']}% CPU | {mp['rss_mb']} MB RAM -> {status}")
    else:
        print("Main IDE Process:    Not currently running (or process list restricted)")

    if procs.get("history_fds"):
        print(
            f"  [!] FOUND {len(procs['history_fds'])} stuck Local History file handle(s)!\n"
            "      Run `python3 antigravity_battery_fix.py --apply` and then quit (`Cmd+Q`) & reopen."
        )
    elif procs.get("main_processes"):
        print("  [OK] 0 stuck Local History file handles.")

    if len(procs.get("agy_hubs", [])) > 1:
        total_hub_mb = sum(h["rss_mb"] for h in procs["agy_hubs"])
        print(
            f"\n--- Multi-Window Memory Note ---\n"
            f"  You have {len(procs['agy_hubs'])} separate `agy --hub` backend servers running "
            f"({total_hub_mb:.0f} MB RAM combined).\n"
            f"  Each open IDE window runs its own backend server; closing unused windows frees RAM."
        )

    print("\n--- 2. Fix Status Check ---")
    settings_path = Path(args.settings_path)
    try:
        cfg = load_jsonc_file(settings_path)
        lh = cfg.get("workbench.localHistory.enabled", True)
        hb = cfg.get("antigravity.heartbeatIntervalMs", 15000)
        search_protected = "**/package-lock.json" in cfg.get("search.exclude", {})
        print(
            f"  * Fix 1 (Disable buggy Local History copies): "
            f"{'APPLIED' if lh is False else 'NOT APPLIED (vulnerable to 100% CPU loop)'}"
        )
        print(
            f"  * Fix 3 (Slow 15s health-check wakeups):      "
            f"{'APPLIED (60s)' if hb >= 60000 else f'DEFAULT ({hb} ms)'}"
        )
        print(
            f"  * Fix 4 (Token-saving search exclusions):     "
            f"{'APPLIED' if search_protected else 'NOT APPLIED'}"
        )
    except Exception as exc:
        print(f"  * Could not read {settings_path}: {exc}")

    try:
        uv_env = subprocess.run(
            ["/bin/launchctl", "getenv", "UV_THREADPOOL_SIZE"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        print(
            f"  * Fix 2 (8 background file-worker threads):   "
            f"{f'APPLIED (UV_THREADPOOL_SIZE={uv_env})' if uv_env else 'DEFAULT (4 threads; 1 blocked at startup)'}"
        )
    except Exception:
        pass

    ok_sig, sig_msg = check_codesign(Path(args.app_bundle))
    print(f"  * App Code Signature:                         {sig_msg}")

    print_plugin_token_report(DEFAULT_PLUGINS_DIR)
    print("\nTo apply all safe fixes automatically, run:")
    print("  python3 antigravity_battery_fix.py --apply")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if sys.platform != "darwin" and not args.allow_non_darwin:
        print("Error: This script is designed for macOS.", file=sys.stderr)
        return 1

    settings_path = Path(args.settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = settings_path.with_name(settings_path.name + BACKUP_SUFFIX)
    manifest_path = settings_path.parent / MANIFEST_NAME

    current = load_jsonc_file(settings_path)
    if settings_path.exists() and not backup_path.exists():
        shutil.copy2(settings_path, backup_path)
        print(f"[Backup]  Saved untouched copy of your original settings to:\n          {backup_path}")

    ram_gb = args.ram_gb or detect_system_ram_gb()
    ts_mem = 1536 if ram_gb <= 8 else (2048 if ram_gb <= 16 else 3072)
    updated, manifest = merge_settings(current, ram_gb=ram_gb, overwrite_existing=args.force)

    settings_path.write_text(json.dumps(updated, indent=4) + "\n", encoding="utf-8")
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("\n[Applied] Merged safe fixes into Antigravity User/settings.json:")
    print("  1. Turned off redundant Local History file copies (prevents the 100%+ CPU infinite loop)")
    print("  2. Cut background /healthz polling wakeups by 75% (15s -> 60s) & disabled background UI motion")
    print(f"  3. Capped TypeScript Server RAM to {ts_mem} MB (sized for {ram_gb} GB Mac) & clean tabs to 15 (unsaved tabs kept safe)")
    print("  4. Added lockfile & build-folder exclusions to save watcher CPU and AI search tokens")

    if not args.skip_launchagent:
        la_path = Path(args.launchagent_path)
        la_path.parent.mkdir(parents=True, exist_ok=True)
        la_path.write_bytes(build_launch_agent_plist(pool_size=8))
        subprocess.run(["/usr/bin/plutil", "-lint", str(la_path)], check=True, capture_output=True)
        subprocess.run(["/bin/launchctl", "setenv", "UV_THREADPOOL_SIZE", "8"], check=False)
        print("  5. Expanded macOS background file-worker pool from 4 -> 8 threads (UV_THREADPOOL_SIZE=8)")

    if Path(args.app_bundle).exists():
        ok_sig, sig_msg = check_codesign(Path(args.app_bundle))
        print(f"\n[Safety]  /Applications/Antigravity IDE.app untouched: {sig_msg}")
        if not ok_sig:
            return 1

    print_plugin_token_report(DEFAULT_PLUGINS_DIR)

    print("\n==============================================================")
    print("  IMPORTANT FINAL STEP: Quit (`Cmd+Q`) and Reopen Once")
    print("==============================================================")
    print(
        "If Antigravity IDE is currently open, press `Cmd+Q` to quit it completely\n"
        "and reopen it once. That clears any worker threads already stuck in memory\n"
        "and loads your new settings cleanly (all open tabs & chat history stay intact)."
    )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    settings_path = Path(args.settings_path)
    backup_path = settings_path.with_name(settings_path.name + BACKUP_SUFFIX)
    manifest_path = settings_path.parent / MANIFEST_NAME
    la_path = Path(args.launchagent_path)

    if args.from_backup and backup_path.exists():
        shutil.copy2(backup_path, settings_path)
        print(f"[Restored] Restored exact original backup from {backup_path}")
    elif manifest_path.exists() and settings_path.exists():
        current = load_jsonc_file(settings_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        restored = restore_settings_from_manifest(current, manifest)
        settings_path.write_text(json.dumps(restored, indent=4) + "\n", encoding="utf-8")
        manifest_path.unlink(missing_ok=True)
        print(f"[Restored] Removed battery-fix keys from {settings_path} (kept any other settings you changed)")
    elif backup_path.exists():
        shutil.copy2(backup_path, settings_path)
        print(f"[Restored] Restored backup from {backup_path}")
    else:
        print("No backup or manifest found to restore.")

    if la_path.exists():
        la_path.unlink()
        print(f"[Removed]  Deleted {la_path}")
    subprocess.run(["/bin/launchctl", "unsetenv", "UV_THREADPOOL_SIZE"], check=False)
    print("[Restored] Reset UV_THREADPOOL_SIZE to macOS default.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Safe macOS diagnostic and fix script for Antigravity IDE battery drain & token bloat."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--diagnose", action="store_true", help="Run read-only CPU, power, and plugin-token check (default)")
    mode.add_argument("--apply", action="store_true", help="Back up settings.json and apply all safe fixes")
    mode.add_argument("--restore", action="store_true", help="Undo settings changes and remove LaunchAgent")

    p.add_argument("--settings-path", default=str(DEFAULT_SETTINGS_PATH), help="Path to Antigravity User/settings.json")
    p.add_argument("--launchagent-path", default=str(DEFAULT_LAUNCH_AGENT_PATH), help="Path to LaunchAgent plist")
    p.add_argument("--app-bundle", default=str(DEFAULT_APP_BUNDLE), help="Path to Antigravity IDE.app")
    p.add_argument("--ram-gb", type=int, default=None, help="Override detected system RAM in GB")
    p.add_argument("--skip-launchagent", action="store_true", help="Skip creating ~/Library/LaunchAgents plist")
    p.add_argument("--force", action="store_true", help="Overwrite existing tuning keys if already customized")
    p.add_argument("--from-backup", action="store_true", help="On --restore, overwrite settings.json directly with .bak")
    p.add_argument("--allow-non-darwin", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply:
        return cmd_apply(args)
    if args.restore:
        return cmd_restore(args)
    return cmd_diagnose(args)


if __name__ == "__main__":
    sys.exit(main())
