# SPEC: KDE Night Brightness — Time-Based DDC/CI Brightness Scheduling
Version: 1.1
Author: Principal-Opus 2026-04-07T13:49
Edited by: Principal-Opus 2026-04-07T14:30 (post-audit fixes: B1-B4, W1-W10)
Status: Audited — ready for implementation

---

## 1. Overview

Night Brightness is a PowerDevil action plugin that schedules hardware display brightness (DDC/CI) based on time of day, using KDE's KNightTime sunrise/sunset framework. It provides a configurable brightness curve with four time periods: daytime, sunset transition, bedtime, and late-night.

The feature fills a gap in KDE Plasma: Night Light (KWin) adjusts color temperature at sunset/sunrise but does not adjust hardware brightness. No existing external tool works on KDE Wayland for DDC/CI brightness scheduling.

**Scope**: PowerDevil daemon action + Night Light KCM UI (cross-package: powerdevil + plasma-workspace).

**Repository**: https://github.com/DefendTheDisabled/KDE-night-light-dimming
**KDE MRs**: plasma/powerdevil!623, plasma/plasma-workspace!6472

## 2. Architecture

### 2.1 Component Diagram

```
plasma-workspace (kcm_nightlight.so)        powerdevil (nightbrightnessaction.so)
  ├─ UI controls in Night Light page          ├─ NightBrightness Action plugin
  ├─ Config in kwinrc [NightColor]            ├─ Reads kwinrc [NightColor]
  ├─ SettingStateBinding for all controls     ├─ KDarkLightScheduleProvider for schedule
  └─ 7 kcfg entries                           ├─ setBrightness() for DDC/CI writes
                                              ├─ KGlobalAccel keybinds (Night Light group)
                                              ├─ Event-driven timer (QTimer::singleShot)
                                              └─ PrepareForSleep D-Bus for suspend/resume
```

### 2.2 Key Constraints

1. **`setBrightness()` is the ONLY working DDC/CI write path for external monitors.** `setDimmingRatio()` sends a dimming hint to KWin, which ignores it for SDR displays (Plasma 6.2+, MR !455). The API appears to work — logs show ratio applied — but no DDC/CI write occurs.

2. **DDC/CI brightness is hardware-persistent.** The monitor retains the last-written VCP 0x10 value across reboots, logouts, and power cycles. Software must actively write a new value to change it.

3. **PowerDevil does NOT persist external monitor brightness across sessions.** `ScreenBrightnessControl` (profile-based brightness) only manages internal (laptop) displays on battery-equipped systems. On desktop PCs with external monitors, no component saves or restores brightness.

4. **DDC/CI writes during early boot can fail catastrophically.** On NVIDIA + KDE Wayland, calling `setBrightness()` before the display backend is fully initialized can kill the DDC/CI connection for the entire session. All writes must be deferred past the initialization window.

5. **DDCutilDisplay has a 1-second debounce timer.** DDC/CI writes are coalesced within this window. NightBrightness update intervals (≥180 seconds during transitions) are well above this.

6. **KWin owns gamma/color. PowerDevil owns brightness.** NightBrightness lives in PowerDevil. It must not call KWin D-Bus for DDC/CI operations.

### 2.3 Dependencies

| Dependency | Purpose |
|------------|---------|
| KNightTime (KDarkLightScheduleProvider) | Sunrise/sunset schedule. Handles geolocation, extreme latitudes, DST, state persistence. |
| KGlobalAccel, KF6::XmlGui | Keyboard shortcut registration |
| org.freedesktop.login1 (D-Bus) | PrepareForSleep signal for suspend/resume detection |
| kwinrc [NightColor] config group | Shared config with Night Light KCM |

## 3. Configuration

All settings live in `~/.config/kwinrc` under the `[NightColor]` group, shared with Night Light color temperature settings.

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `NightBrightnessEnabled` | Bool | false | — | Master enable. Disabled by default (opt-in). |
| `NightBrightnessDaytimePct` | Int | 100 | 5-100 | Daytime brightness as % of monitor max. Applied once at sunrise/boot. |
| `NightBrightnessBedtimePct` | Int | 40 | 5-100 | Bedtime brightness as % of monitor max. |
| `NightBrightnessLateNightPct` | Int | 20 | 5-100 | Late-night brightness as % of monitor max. |
| `NightBrightnessBedtimeOffsetMin` | Int | 120 | 0-720 | Minutes after sunset to reach bedtime level. |
| `NightBrightnessLateNightOffsetMin` | Int | 180 | 0-720 | Minutes after sunset to reach late-night level. |
| `NightBrightnessTransitionMin` | Int | 30 | 1-120 | Duration of the bedtime-to-late-night transition ramp in minutes. Evening and morning transitions follow KNightTime's own transition timing (from Night Light color temperature settings). |

**Validation**:
- `LateNightOffsetMin` must be > `BedtimeOffsetMin`. If not, runtime clamps `LateNightOffsetMin = BedtimeOffsetMin + TransitionMin`.
- **Brightness ordering** (DaytimePct ≥ BedtimePct ≥ LateNightPct) is NOT enforced. Users may set inverted values (e.g., brighter at night than day). This produces counterintuitive but functional behavior. No crash or undefined state results. Future enhancement: UI tooltip warning when ordering is inverted.

**State persistence**: Schedule state saved to `KSharedConfig::openStateConfig()` under `[NightBrightness]` → `ScheduleState` for faster provider initialization.

## 4. Brightness Curve Model

### 4.1 Four-Period Curve

```
Ratio
 daytime ┤━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
         │            DAY                ┃╲  evening
         │                               ┃  ╲ transition
 bedtime ┤                               ┃    ━━━━━━━━━━━━━┓ BEDTIME
         │                               ┃                  ┃╲  bedtime→late
         │                               ┃                  ┃  ╲ transition
latenight┤                               ┃                  ┃    ━━━━━━━━ LATE NIGHT
         │  morning                      ┃                  ┃            ╱
         │  transition ╱                 ┃                  ┃           ╱
         ├────────────┼─────────────────┼──────────┼───────┼──────────┼──
       sunrise     sunset           +offset1  +offset2   sunrise
              (morning.end)    (evening.end)
```

### 4.2 Ratio Computation

Given `now`, and `schedule` from `KDarkLightScheduleProvider`:

| Time Period | Condition | Ratio |
|-------------|-----------|-------|
| Morning transition in progress | `nextTransition.type==Morning && InProgress` | `lerp(lateNightRatio, daytimeRatio, progress)` |
| Daytime | `prevTransition.type==Morning` | `daytimeRatio` |
| Evening transition in progress | `nextTransition.type==Evening && InProgress` | `lerp(daytimeRatio, bedtimeRatio, progress)` |
| Post-sunset hold | `secsSinceSunset < bedtimeOffsetMin*60` | `bedtimeRatio` |
| Bedtime→late-night transition | `secsSinceSunset < bedtimeStart + transitionSecs` | `lerp(bedtimeRatio, lateNightRatio, progress)` |
| Late-night hold | Otherwise during night | `lateNightRatio` |
| Extreme latitude (no sunrise/sunset) | `!prevTransition \|\| !nextTransition` | `daytimeRatio` (respects user preference) |

Where:
- `daytimeRatio = DaytimePct / 100.0`
- `bedtimeRatio = BedtimePct / 100.0`
- `lateNightRatio = LateNightPct / 100.0`

### 4.3 Update Scheduling

- **During transitions**: Updates every `max(transitionMin*60/steps, 180)` seconds, where steps = ratio change / 0.02 (2% quantum)
- **During holds**: Timer sleeps until next transition boundary (could be hours)
- **Minimum spacing**: 180 seconds between any two `setBrightness()` calls
- **Event-driven**: `QTimer::singleShot` per update, not polling

## 5. Behavioral Specification

### 5.1 Boot / Login (Any Time)

1. PowerDevil starts → loads NightBrightness action via `loadAction()`
2. `loadAction()` sets `m_firstLoadPending = true`
3. `applyCurrentBrightness()` detects first-load flag
4. Schedules deferred `applyBrightnessNow()` in 5 seconds (DDC/CI bus safety — timing issue, not value-dependent)
5. After 5 seconds: re-checks guards (loaded, not inhibited), computes current ratio, applies to all displays
6. Skips DDC/CI write per display if `current == target` (avoids redundant I2C traffic)
7. If no displays registered yet, retries once after 5 more seconds
8. `scheduleNextUpdate()` determines next wake time:
   - **If daytime**: sleeps until evening transition begins (hours away) — natural one-shot behavior
   - **If nighttime**: sleeps until next 2% ratio change (~180 seconds during transitions, or until tier boundary during holds)

### 5.2 Daytime Hold (One-Shot Then Dormant)

1. After deferred boot application (§5.1) or after morning transition completes (§5.4), ratio is stable at `daytimeRatio`
2. `scheduleNextUpdate()` scans ahead, finds no ≥2% change until evening → timer sleeps for hours
3. NightBrightness does NOT re-apply brightness during daytime — user adjustments via slider/widget persist
4. Timer wakes when evening transition approaches (ratio begins to decrease)

### 5.3 Evening Transition (Sunset)

1. `scheduleNextUpdate()` timer fires as evening transition begins
2. `computeRatioForTime()` returns lerp(daytimeRatio, bedtimeRatio, progress)
3. Brightness decreases progressively over the KNightTime evening transition duration
4. Continues through bedtime hold → bedtime→late-night transition (using `TransitionMin`) → late-night hold

**Note on transition durations**: Evening and morning transitions use KNightTime's own transition timing (from Night Light color temperature settings). Only the bedtime→late-night transition uses `NightBrightnessTransitionMin`. These are different clocks.

**Short-night edge case**: If sunrise begins before late-night is reached (very short summer nights at high latitudes), morning transition lerps from whatever the current ratio is — which may be bedtimeRatio rather than lateNightRatio. The `computeRatioForTime()` implementation handles this because it checks `nextTransition.type==Morning && InProgress` before the nighttime tier logic.

### 5.4 Morning Transition (Sunrise)

1. Timer fires at morning transition start
2. `computeRatioForTime()` returns lerp(lateNightRatio, daytimeRatio, progress)
3. Brightness increases progressively over the KNightTime morning transition duration
4. When transition completes, ratio reaches daytimeRatio
5. Enters daytime hold (§5.2) — timer sleeps until next evening

### 5.5 Suspend / Resume

1. System suspends — DDC/CI hardware retains current brightness
2. On resume: `PrepareForSleep(false)` D-Bus signal fires
3. `onPrepareForSleep()` sets `m_firstLoadPending = true`, calls `applyCurrentBrightness()`
4. Deferred write in 5 seconds (same as boot — DDC/CI may need re-initialization after resume)
5. After delay: applies correct brightness for current time period

### 5.6 Monitor Hotplug

1. `displayAdded` signal fires (after DDC/CI probe succeeds — display backend confirmed ready)
2. `onDisplayAdded()` computes current ratio via `computeRatioForTime()` (returns correct value for any time period)
3. Applies computed brightness to new display (unified path — no day/night branching)
4. Skips write if display is already at target brightness
5. No deferred timer needed — `displayAdded` proves DDC/CI is operational for that display

### 5.7 Manual Override (Keybinds)

- **Alt+PgUp**: Increase brightness by 5% of max (all displays)
- **Alt+PgDn**: Decrease brightness by 5% of max (all displays)
- Sets `m_manualOverrideActive = true`
- Override persists until the scheduled ratio changes by >5% (tier boundary crossed)
- During override, `applyCurrentBrightness()` skips scheduled updates
- Override cleared on `loadAction()` (profile/config change)

### 5.8 Inhibition

- Respects `PolicyAgent::ChangeScreenSettings`
- When inhibited (presentation mode, video playback): pauses timer, does not change brightness
- When inhibition cleared: resumes scheduled brightness

### 5.9 Feature Disabled

- `loadAction()` returns `false`
- Timer stopped, no brightness changes
- User controls brightness freely via slider/widget
- No restore on disable — user adjusts manually (consistent with "NightBrightness steps aside")

## 6. Keyboard Shortcuts

| Action | Default Shortcut | Component | Display Name |
|--------|-----------------|-----------|--------------|
| Increase Night Brightness | Alt+PgUp | `nightbrightness` | Night Light |
| Decrease Night Brightness | Alt+PgDn | `nightbrightness` | Night Light |

Registered via `KGlobalAccel::setGlobalShortcut()`. Shortcuts appear in System Settings → Shortcuts under "Night Light" group.

## 7. UI Specification (Night Light KCM)

Controls appear in System Settings → Display & Monitor → Night Light, below the color temperature controls, gated by the `NightBrightnessEnabled` checkbox.

| Control | Type | Binding | Visibility |
|---------|------|---------|------------|
| "Dim screen brightness at night" | CheckBox | `NightBrightnessEnabled` | Always (when Night Light active) |
| "Daytime brightness" | Slider 5-100% | `NightBrightnessDaytimePct` | When enabled |
| "Bedtime brightness" | Slider 5-100% | `NightBrightnessBedtimePct` | When enabled |
| "Bedtime starts" | SpinBox 0-720min | `NightBrightnessBedtimeOffsetMin` | When enabled |
| "Late night brightness" | Slider 5-100% | `NightBrightnessLateNightPct` | When enabled |
| "Late night starts" | SpinBox 0-720min | `NightBrightnessLateNightOffsetMin` | When enabled |
| "Transition duration" | SpinBox 1-120min | `NightBrightnessTransitionMin` | When enabled |

All controls use `KCM.SettingStateBinding` for KDE Defaults/Reset integration.

SpinBox `textFromValue` displays relative time: "2h 0min after sunset", "45 min after sunset".

Slider labels: "Bright (no dimming)" on left, "Dim" on right.

## 8. Known Limitations

| Limitation | Detail |
|------------|--------|
| No per-display brightness config | All connected displays get the same percentage. Future enhancement. |
| DDC/CI latency | ~1 second per write (I2C bus speed). Inherent to DDC/CI, not fixable in software. |
| Laptop backlights untested | Feature designed for DDC/CI external monitors. Laptop backlights use a different brightness path and may behave differently. |
| SDR only | KWin's HDR brightness path is separate. Feature targets SDR displays. |
| 5-second boot delay | User sees stale brightness for ~5 seconds after login before brightness is applied. Applies to all boots (day and night). Acceptable tradeoff vs DDC/CI bus failure. |
| KDE slider adjustment during day not synced back to config | If user sets 70% via slider after Night Brightness applied 80%, the 80% config is not updated. Next boot applies 80% again. This is by design — Night Brightness is a schedule, not a sync. |
| Slider adjustment during 5-second delay may be overwritten | Brightness changes made in the first 5 seconds after login may be overwritten by the deferred scheduled application. Unusual edge case (user rarely adjusts slider within seconds of login). |
| Manual override only covers NightBrightness keybinds | Adjusting brightness via the KDE system tray slider during nighttime will still be overwritten on next scheduled tick. Full slider integration (via `ScreenBrightnessController::brightnessChanged` with source filtering) deferred to v3. |
| Override clearing is 5% drift, not strict tier boundary | During gradual transitions, override clears after scheduled ratio drifts >5% from override point (~6-9 minutes). Acceptable for v2; strict phase-based clearing deferred to v3. |
| Short-night latitude edge case | If sunrise begins before late-night tier is reached, morning transition lerps from current ratio (may be bedtimeRatio). Functional but transition shape differs from the nominal 3-tier curve. |

## 9. Patch Delivery

Feature delivered as two Python patch scripts that modify upstream KDE source trees:

| Script | Target Package | Creates/Modifies |
|--------|---------------|-----------------|
| `patch-powerdevil-nightbrightness.py` | powerdevil | Creates nightbrightness.h, nightbrightness.cpp, plugin JSON. Modifies CMakeLists.txt (2), adds KNightTime dependency. |
| `patch-plasma-nightbrightness.py` | plasma-workspace | Modifies nightlightsettings.kcfg (adds 7 entries), main.qml (adds brightness UI section). |

**PKGBUILD integration**: Patch scripts run in `prepare()` phase. IgnorePkg holds `powerdevil` and `plasma-workspace` to prevent rolling updates from overwriting patches.

**Build**: `makepkg -sf` in each package directory. Install via `sudo pacman -U <package>.pkg.tar.zst`.
