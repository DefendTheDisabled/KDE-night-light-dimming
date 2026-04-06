# KDE Plasma Brightness Control: Lessons Learned
Author: Principal-Opus 2026-04-06
Subject: Architecture pitfalls, codebase patterns, and UX principles discovered while implementing time-based DDC/CI brightness scheduling for KDE Plasma 6.

---

## 1. Executive Summary

Implementing a "Night Brightness" feature (time-based DDC/CI hardware brightness scheduling) for KDE Plasma took 4 failed approaches before succeeding. Each failure revealed a non-obvious architectural constraint in KDE's brightness stack. This report documents what we learned so future agents avoid repeating these mistakes.

**The working solution**: PowerDevil Action plugin using `ScreenBrightnessController::setBrightness()` directly, with UI in the Night Light KCM (plasma-workspace), schedule from KDarkLightScheduleProvider (KNightTime framework), config in kwinrc.

**Time cost of mistakes**: ~6 hours of iterative failure before the architecture was understood correctly.

---

## 2. The KDE Brightness Stack (What We Learned)

### 2.1 Two Separate Brightness Mechanisms

KDE Plasma has TWO ways to affect display brightness, and they DO NOT behave the same way:

| Mechanism | API | Effect on DDC/CI External Monitors |
|-----------|-----|-----------------------------------|
| `setBrightness(displayId, value)` | Writes VCP code 0x10 to monitor via DDC/CI. **Actually changes hardware brightness.** | ✅ WORKS |
| `setDimmingRatio(dimmingId, ratio)` / `setDimmingMultiplier(ratio)` | Sends dimming hint to KWin via Wayland protocol. KWin applies as SDR brightness overlay. | ❌ IGNORED for SDR external monitors (Plasma 6.2+, MR !455) |

**Critical**: `setDimmingRatio()` calls `setDimmingMultiplier()` on the KWin brightness backend, which returns `supportsDimmingMultiplier() = true`. The API appears to work — it stores the ratio, logs the change, and schedules updates. But KWin silently discards the dimming multiplier for SDR displays. There is NO error, NO warning, NO indication of failure.

This means DimDisplay's idle dimming via `setDimmingRatio("DimDisplay", 0.3)` also does not work on DDC/CI external monitors in SDR mode. It only works on laptop backlights and HDR displays.

### 2.2 The KWin ↔ PowerDevil Brightness Protocol

```
PowerDevil                           KWin
  ├─ DDCutilDisplay (i2c:8)            ├─ ExternalBrightnessControl
  │   └─ Direct DDC/CI writes         │   └─ Wayland kde-external-brightness-v1
  │                                    │
  └─ KWinDisplayBrightness             │
      ├─ setBrightness() ────────────► setBrightness() → DDC/CI ✅
      └─ setDimmingMultiplier() ─────► setDimming() → IGNORED for SDR ❌
```

PowerDevil announces displays to KWin, which creates `kwin:HDMI-A-2` type display IDs. The slider and `setBrightness()` go through this path and DO write DDC/CI. But `setDimmingMultiplier()` goes to a separate `setDimming()` call that KWin ignores.

### 2.3 Why This Is Not Documented

MR !455 (Jakob Petsovits, 2024) disabled KWin's SDR brightness control to prevent KWin from overriding hardware brightness settings. The intent was correct — but the side effect is that `setDimmingRatio()` becomes a no-op for DDC/CI monitors. This isn't documented in PowerDevil's API comments, the header files, or the commit message. The only way to discover it is by testing on actual hardware and reading the KWin source.

---

## 3. Failed Approaches (In Order)

### 3.1 clight (External Tool)
**What**: AUR package for ambient/scheduled brightness control.
**Why it failed**: Requires `zwlr_gamma_control_manager_v1` Wayland protocol. KDE Plasma does NOT expose wlr-gamma-control (that's a wlroots protocol for Sway/Hyprland). SEGFAULT on start.
**Lesson**: Check Wayland protocol compatibility before evaluating tools. KDE Plasma ≠ wlroots.

### 3.2 KWin Night Light Patch
**What**: 471-line patch adding brightness scheduling to KWin's nightlightmanager.cpp, calling PowerDevil's D-Bus for DDC/CI brightness.
**Why it failed**: 
1. KWin calling PowerDevil's D-Bus for DDC/CI creates I2C bus contention with gamma ramp changes on NVIDIA
2. DDC/CI failures cause PowerDevil to emit `DisplayRemoved` → brightness controls disappear from UI entirely
3. 30-second DDC/CI polling is far too aggressive for I2C bus
4. KWin SDR brightness was deliberately disabled (MR !455) — KWin is architecturally NOT supposed to control DDC/CI
**Lesson**: KWin owns gamma/color. PowerDevil owns brightness. Don't cross-call between them for DDC/CI operations.

### 3.3 PowerDevil Action with setDimmingRatio()
**What**: New Action plugin using `setDimmingRatio()` — the API DimDisplay uses for idle dimming.
**Why it failed**: `setDimmingRatio()` internally calls `setDimmingMultiplier()` on the KWin brightness backend. KWin ignores the dimming multiplier for SDR external monitors. The API appears to work (logs show ratio applied, internal state updated) but no DDC/CI write occurs.
**Lesson**: `setDimmingRatio()` is a dead code path for DDC/CI external monitors in Plasma 6.2+. Use `setBrightness()` directly.

### 3.4 PowerDevil Action with setBrightness() — SUCCESS
**What**: Same Action plugin but calling `setBrightness()` directly with absolute values computed from the schedule.
**Why it works**: `setBrightness()` goes through `KWinDisplayBrightness::applyPendingBrightness()` → `m_output->setBrightness()` which IS the DDC/CI write path. This is the same path the slider uses.

---

## 4. KDE QML / KCM Patterns (What Agents Must Know)

### 4.1 SettingStateBinding, NOT SettingHighlighter

KDE Plasma 6 uses `KCM.SettingStateBinding` for connecting UI controls to KConfig settings. The component `KCM.SettingHighlighter` does NOT have a `settingName` property — using it crashes the entire KCM page with:

```
Cannot assign to non-existent property "settingName"
```

**Correct pattern:**
```qml
QQC2.CheckBox {
    checked: kcm.nightLightSettings.nightBrightnessEnabled
    onToggled: kcm.nightLightSettings.nightBrightnessEnabled = checked

    KCM.SettingStateBinding {
        configObject: kcm.nightLightSettings
        settingName: "NightBrightnessEnabled"
        extraEnabledConditions: kcm.nightLightSettings.active
    }
}
```

Every control that binds to a config setting needs its own `SettingStateBinding`. Without it, the KDE "Defaults" / "Reset" functionality won't work, and the setting won't show the orange non-default indicator.

### 4.2 QML Is Compiled Into .so Files

KDE KCM QML files are compiled into the `.so` plugin binary via Qt's resource system. They are NOT installed as separate `.qml` files on disk. You cannot verify QML changes by reading files from `/usr/share/` — you must rebuild the package and check via `strings` (though compiled QML bytecode won't show as plaintext).

### 4.3 SpinBox valueFromText

QML `QQC2.SpinBox` with `editable: true` requires a proper `valueFromText` inverse function. `valueFromText: function(text) { return value; }` silently breaks — it returns the current value and ignores user input. Either implement proper parsing or set `editable: false`.

### 4.4 Section Patterns

PowerDevil's ProfileConfig.qml uses `Item { Kirigami.FormData.isSection: true }` for section headers, NOT `Kirigami.Separator`. Night Light's main.qml uses `Kirigami.FormLayout` with `twinFormLayouts`. Match the existing file's patterns exactly.

---

## 5. KGlobalAccel / Keybind Patterns

### 5.1 Component Name Determines UI Grouping

In KDE's System Settings → Shortcuts, shortcuts are grouped by component. The `KActionCollection::setComponentName()` and `setComponentDisplayName()` determine which group the shortcut appears in.

**Wrong**: Using `"Power Management"` as display name groups Night Brightness keybinds with power management shortcuts (Suspend, Brightness Up/Down) instead of with Night Light.

**Right**: Use a dedicated component name and display name:
```cpp
actionCollection->setComponentName(u"nightbrightness"_s);
actionCollection->setComponentDisplayName(i18nc("...", "Night Light"));
```

### 5.2 Stale Keybind Entries Persist

When a plugin registers shortcuts via `KGlobalAccel::setGlobalShortcut()`, the entries are written to `~/.config/kglobalshortcutsrc`. If the plugin is later removed or the component name changes, the OLD entries remain in the config file. KDE's Shortcuts UI will show stale entries from components that no longer exist.

**Cleanup**: Use `kwriteconfig6 --file kglobalshortcutsrc --group <component> --key <action> --delete` to remove stale entries. Then relog for KGlobalAccel to reload.

### 5.3 Empty Active Binding on First Registration

When `KGlobalAccel::setGlobalShortcut(action, shortcut)` registers a new shortcut, it may store the shortcut as the DEFAULT but leave the ACTIVE binding empty in kglobalshortcutsrc if the component was previously known with empty bindings. Delete the entire key before first registration to ensure the default becomes active.

---

## 6. UX Principles (What the User Corrected)

These are corrections the user had to make during development because agents made wrong assumptions:

### 6.1 Feature Controls Go Where Users Expect Them

Night Brightness is a Night Light feature — its controls belong in Night Light settings (Display & Monitor), not Power Management. Even though the daemon lives in PowerDevil, the UI must be where users would look for it. This required patching plasma-workspace (a different package) rather than just PowerDevil.

**Principle**: UI placement follows user mental model, not code architecture.

### 6.2 Keybinds Are Core, Not Optional

The user's spec explicitly required Alt+PgUp/PgDn keybinds for brightness adjustment. Associates recommended "skip keybinds in v1" for simplicity. The user rejected this — keybinds are part of the core design for f.lux parity and usage efficiency. Don't second-guess explicit user requirements.

**Principle**: User spec requirements are not negotiable unless technically impossible.

### 6.3 Users Expect Absolute Brightness, Not Multipliers

When the UI says "bedtime brightness: 40%", users expect 40% DDC/CI hardware brightness — not "40% of whatever the slider is at." The multiplier model (`setDimmingRatio`) is an internal implementation concept that confuses users. The working implementation uses absolute values: `brightness = maxBrightness × percentage`.

**Principle**: UI percentages must map to observable outcomes. Internal implementation models that diverge from user expectations are bugs, not features.

### 6.4 Group All Feature Touchpoints Together

If the feature UI is in Night Light, the keybinds should be in Night Light's shortcuts group, the config should be in Night Light's config file (kwinrc), and the documentation should reference Night Light. Scattering touchpoints across Power Management, Window Management, and Night Light creates confusion.

**Principle**: A feature's UI, shortcuts, config, and documentation all belong to the same logical group.

---

## 7. Architecture Summary (What Works)

```
plasma-workspace (kcm_nightlight.so)        powerdevil (nightbrightnessaction.so)
  ├─ UI controls in Night Light page          ├─ NightBrightness Action plugin
  ├─ Config in kwinrc [NightColor]            ├─ Reads kwinrc [NightColor]
  ├─ SettingStateBinding for all controls     ├─ KDarkLightScheduleProvider for schedule
  └─ 6 new kcfg entries                      ├─ setBrightness() for DDC/CI writes
                                              ├─ KGlobalAccel keybinds (Night Light group)
                                              └─ Event-driven timer (QTimer::singleShot)
```

**Config authority**: kwinrc `[NightColor]` group — single source of truth.
**Schedule authority**: KNightTime (knighttimed daemon) — handles sunrise/sunset, DST, extreme latitudes.
**Brightness authority**: PowerDevil via `setBrightness()` — actual DDC/CI VCP 0x10 writes.
**UI authority**: Night Light KCM — users find it where they expect it.

---

## 8. For Future Agents: Pre-Implementation Checklist

Before writing any KDE brightness-related code:

1. **Verify the DDC/CI write path**: Does your API call result in `Set screen brightness of "kwin:..." to X / Y` in PowerDevil's debug log? If not, you're in a dead code path.
2. **Enable debug logging**: Set `org.kde.powerdevil=true` in `~/.config/QtProject/qtlogging.ini` and restart PowerDevil.
3. **Check which display backend is active**: `kwin:HDMI-A-2` (KWin Wayland) or `i2c:8` (DDCutil direct). The KWin backend is used for Plasma 6.2+.
4. **Test with `setBrightness()` first**: Before using `setDimmingRatio()`, verify that `setDimmingRatio` actually changes hardware brightness on your specific display type. On SDR external monitors, it doesn't.
5. **Read the existing QML patterns**: Don't guess at KDE QML components. Read the actual file you're patching and match its patterns exactly.
6. **Check for stale keybind entries**: After changing KGlobalAccel component names, delete old entries from `~/.config/kglobalshortcutsrc`.
7. **Use Associates for architecture review BEFORE implementing**: The KWin→PowerDevil→DDC/CI interaction is non-obvious. Get multiple models to review the plan before writing code.
