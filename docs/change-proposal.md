# Change Proposal: Time-Based Brightness Scheduling for KDE PowerDevil
Author: Principal-Opus 2026-04-05
Edited by: Principal-Opus 2026-04-06T01:00
Version: v3 (post-implementation, corrected from v2 based on runtime failures)

**IMPORTANT**: v2 of this CP recommended `setDimmingRatio()`. This is WRONG for DDC/CI external monitors — KWin ignores SDR dimming multipliers (Plasma 6.2+, MR !455). The working implementation uses `setBrightness()` directly. See `Artifacts_Agents/report-kde-brightness-lessons-learned.md` for full analysis of why v2's architecture failed.

---

## 1. Problem Statement

KDE Plasma has no time-based DDC/CI brightness scheduling. Night Light (KWin) adjusts color temperature at sunset/sunrise but does NOT adjust hardware brightness. No existing external tool works on KDE Wayland:

- **clight**: Requires `zwlr_gamma_control_manager_v1` — KDE Plasma doesn't expose it. SEGFAULT.
- **f.lux**: Gamma manipulation only. No DDC/CI hardware brightness control.
- **ddcutil+cron**: No sunrise/sunset awareness. DDC/CI contention with PowerDevil.
- **KWin Night Light patch** (our prior attempt): Architectural violation — KWin calling PowerDevil D-Bus for DDC/CI caused I2C bus contention, brightness controls disappeared. SDR brightness deliberately disabled in KWin (Plasma 6.2, MR !455).

## 2. Proposed Solution

A new **PowerDevil Action plugin** (`NightBrightness`) that schedules brightness dimming using:
- **`setDimmingRatio()`** — PowerDevil's existing multiplier-overlay API (same as DimDisplay idle dimming)
- **`KDarkLightScheduleProvider`** — KDE's `knighttime` framework for sunrise/sunset scheduling (authored by Vlad Zahorodnii, KWin lead)

### Design Principles

1. **`setBrightness()` directly, not `setDimmingRatio()`** — sets absolute DDC/CI hardware brightness. When UI says "20% brightness", the monitor is at 20%. ~~v2 recommended setDimmingRatio — this fails on DDC/CI external monitors because KWin ignores SDR dimming multipliers.~~
2. **Lives inside PowerDevil** — no D-Bus round-trips, no external process, no I2C contention.
3. **Uses KNightTime library** — decoupled from Night Light color temperature. Works even if Night Light is disabled. Handles sunrise/sunset calculation, extreme latitudes, DST, state persistence.
4. **Event-driven** — compute next brightness change point, `QTimer::singleShot` until then. No polling.
5. **UI in Night Light KCM** — users expect brightness scheduling alongside color temperature scheduling. UI placement follows user mental model, not code architecture. This requires patching plasma-workspace (separate package from PowerDevil).
6. **Config in kwinrc** — shared with Night Light KCM. Single source of truth in `[NightColor]` group.
7. **Keybinds are core, not optional** — Alt+PgUp/PgDn for manual override. Registered under "Night Light" shortcuts group (not "Power Management"). Required for f.lux parity.
8. **Absolute brightness values** — UI percentages map directly to DDC/CI hardware levels. "40% brightness" means `maxBrightness × 0.4` sent to the monitor. Not a multiplier on the slider position.

## 3. Architecture

### Component Placement

```
PowerDevil daemon (org.kde.Solid.PowerManagement)
├── daemon/
│   ├── controllers/
│   │   └── screenbrightnesscontroller.cpp  ← setDimmingRatio() (UNTOUCHED)
│   ├── actions/bundled/
│   │   ├── dimdisplay.cpp                  ← EXISTING idle dim (our template)
│   │   ├── nightbrightness.h               ← NEW
│   │   ├── nightbrightness.cpp             ← NEW
│   │   ├── powerdevilnightbrightnessaction.json  ← NEW
│   │   └── CMakeLists.txt                  ← MODIFIED (add plugin)
│   ├── powerdevilsettingsdefaults.h/cpp    ← MODIFIED (add defaults)
│   └── CMakeLists.txt                      ← MODIFIED (add KNightTime dep)
├── PowerDevilProfileSettings.kcfg          ← MODIFIED (add config entries)
├── CMakeLists.txt                          ← MODIFIED (find_package KNightTime)
└── kcm/ui/ProfileConfig.qml               ← MODIFIED (add UI controls)
```

### Data Flow

```
KNightTime (knighttimed daemon)         PowerDevil
  KDarkLightScheduleProvider              NightBrightness action
  ├─ schedule().nextTransition(now) ───►  ├─ evening start/end (sunset times)
  ├─ schedule().previousTransition() ──►  ├─ morning start/end (sunrise times)
  ├─ transition.progress(now) ──────────► ├─ smooth interpolation during transitions
  └─ scheduleChanged() signal ──────────► ├─ recalculate on schedule update
                                          ├─ computeRatioForTime(now)
                                          ├─ setDimmingRatio("NightBrightness", ratio)
                                          └─ QTimer::singleShot → next change point
```

No Night Light dependency. No D-Bus round-trips.

### KDarkLightScheduleProvider API (Verified — `/usr/include/KNightTime/`)

| Class | Key Methods |
|-------|-------------|
| `KDarkLightScheduleProvider` | `schedule()`, `scheduleChanged()` signal, `state()` persistence |
| `KDarkLightSchedule` | `nextTransition(dateTime)`, `previousTransition(dateTime)`, `cycles()` |
| `KDarkLightTransition` | `type()` (Morning/Evening), `startDateTime()`, `endDateTime()`, `progress(dateTime)` 0.0-1.0, `test(dateTime)` (Upcoming/InProgress/Passed) |
| `KDarkLightCycle` | `morning()`, `evening()`, `noonDateTime()` |

Provider handles: geolocation, custom fixed times, extreme latitudes (`std::nullopt`), DST, clock changes, state persistence across reboots, async refresh. Default fallback: 6:00 AM / 6:00 PM.

`knighttimed` daemon verified running on Praxis: `org.kde.NightTime` on session bus.

## 4. Brightness Curve Model

### 3-Tier Piecewise Linear

```
Ratio
 1.0 ┤━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
     │              DAY               ┃╲  sunset
     │                                ┃  ╲ transition
 0.4 ┤                                ┃    ━━━━━━━━━━━━━┓ BEDTIME
     │                                ┃                  ┃╲  bedtime
     │                                ┃                  ┃  ╲ transition
 0.2 ┤                                ┃                  ┃    ━━━━━━━━ LATE NIGHT
     │                                ┃                  ┃            ╱
     ├─────────────┼─────────────────┼──────────┼───────┼──────────┼──
   sunrise       sunset          sunset+2h  sunset+3h  sunrise
                evening.end()
```

### Configuration Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `NightBrightnessEnabled` | Bool | false | — | Master enable (disabled by default) |
| `NightBrightnessBedtimePct` | Int | 40 | 5-100 | Bedtime brightness as % of user's setting |
| `NightBrightnessLateNightPct` | Int | 20 | 5-100 | Late-night brightness as % |
| `NightBrightnessBedtimeOffsetMin` | Int | 120 | 0-720 | Minutes after sunset to reach bedtime level |
| `NightBrightnessLateNightOffsetMin` | Int | 180 | 0-720 | Minutes after sunset to reach late-night level |
| `NightBrightnessTransitionMin` | Int | 30 | 1-120 | Duration of each transition ramp |

**Rationale for Int percentages (not Double ratios):** Consistent with PowerDevil's existing patterns (brightness values are Int). Simpler for QML sliders. Conversion to ratio: `double ratio = pct / 100.0`.

**No configurable day brightness:** Day is always 100% (ratio 1.0). Configuring day brightness would duplicate the existing brightness slider's function.

### Transition Logic

Given `evening = schedule.previousTransition()` where type == Evening:

```
sunsetEnd = evening.endDateTime()
bedtimeStart = sunsetEnd + bedtimeOffsetMin
bedtimeEnd = bedtimeStart + transitionMin
lateStart = sunsetEnd + lateNightOffsetMin  
lateEnd = lateStart + transitionMin
```

| Time Range | Ratio | Notes |
|------------|-------|-------|
| sunrise+transition → sunset | 1.0 | Full brightness (day) |
| sunset → sunset+transition | lerp(1.0, bedtimePct/100) | Evening transition |
| sunset+transition → bedtimeStart | bedtimePct/100 | Bedtime hold |
| bedtimeStart → bedtimeEnd | lerp(bedtime, lateNight) | Bedtime→late-night ramp |
| bedtimeEnd → next sunrise | lateNightPct/100 | Late-night hold |
| sunrise → sunrise+transition | lerp(lateNight, 1.0) | Morning transition |

**High-latitude edge case:** If night is too short for all tiers, skip intermediate ones. If `lateNightOffsetMin > time_between_sunset_and_sunrise`, clamp to latest tier that fits. If `schedule.nextTransition()` returns `std::nullopt` (polar day/night), hold current ratio.

### Update Frequency During Transitions

- **Steady-state** (holds): timer sleeps until next transition boundary — could be hours
- **During transitions**: update every `max(transitionMin*60/steps, 180)` seconds where steps = `abs(startPct - endPct) / 2` (2% quantum)
- Example: 30-min transition, 100→40% = 30 steps at 2% each, interval = max(60, 180) = 180s = ~10 updates
- DDCutilDisplay debounce: 1 second (not 500ms as originally stated). Our ≥180s spacing is well within safe range.

## 5. Brightness Control Mechanism

### ~~setDimmingRatio~~ → setBrightness (v3 Correction)

**v2 recommended `setDimmingRatio()`. This DOES NOT WORK for DDC/CI external monitors.**

Root cause: `setDimmingRatio()` calls `KWinDisplayBrightness::setDimmingMultiplier()` which sends a dimming hint to KWin via Wayland protocol. KWin ignores this for SDR displays (Plasma 6.2+, MR !455). The API appears to work — logs show ratio applied, internal state updated — but no DDC/CI write occurs. See `report-kde-brightness-lessons-learned.md` for full analysis.

**Working approach**: Call `ScreenBrightnessController::setBrightness(displayId, absoluteValue, ...)` directly. This goes through `KWinDisplayBrightness::applyPendingBrightness()` → `m_output->setBrightness()` which IS the DDC/CI VCP 0x10 write path. Same path the brightness slider uses.

```cpp
void NightBrightness::applyCurrentBrightness() {
    double ratio = computeRatioForTime(QDateTime::currentDateTime());
    auto *ctrl = core()->screenBrightnessController();
    for (const QString &id : ctrl->displayIds()) {
        int target = qRound(ctrl->maxBrightness(id) * ratio);
        ctrl->setBrightness(id, target, SOURCE_NAME, u"scheduled"_s, 
                           ScreenBrightnessController::SuppressIndicator);
    }
}
```

### DDC/CI Safety

`setBrightness()` writes are coalesced by DDCutilDisplay's 1-second debounce timer (not 500ms as previously stated). Our updates occur at most every 180 seconds during transitions — well within safe limits.

### Hotplug Handling

`setBrightness()` must be called per-display. Listen to `displayAdded` and reapply:

```cpp
connect(core()->screenBrightnessController(), &ScreenBrightnessController::displayAdded,
        this, &NightBrightness::onDisplayAdded);
```

**`std::min()` stacking** — most restrictive limit wins:

| DimDisplay | NightBrightness | Effective | State |
|---|---|---|---|
| 1.0 | 1.0 | 1.0 | Day, active |
| 1.0 | 0.4 | 0.4 | Bedtime, active |
| 0.3 | 0.4 | 0.3 | Bedtime, idle (idle dominates) |
| 1.0 | 0.2 | 0.2 | Late night, active |
| 0.3 | 0.2 | 0.2 | Late night, idle (night dominates) |

Idle dim never BRIGHTENS at night. Night brightness never fights idle dim. Clean.

### Hotplug Handling

`setDimmingRatio()` applies to all currently connected displays. Newly connected displays start at ratio 1.0. Action must listen to `displayAdded` signal and reapply current ratio:

```cpp
connect(core()->screenBrightnessController(), &ScreenBrightnessController::displayAdded,
        this, [this]() { applyCurrentBrightness(); });
```

## 6. Resolved Design Questions

| Question | Resolution | Rationale |
|----------|-----------|-----------|
| PowerDevil vs standalone? | **PowerDevil** | Owns brightness. Internal API. No D-Bus round-trips. 3 Associates converged. |
| Schedule: Night Light D-Bus vs KNightTime? | **KNightTime** (KDarkLightScheduleProvider) | Decoupled from color temperature. Handles all edge cases. Authored by KWin lead. |
| 2-tier vs 3-tier? | **3-tier** (day/bedtime/latenight) | User's spec. Reducible to 2-tier by setting bedtime==latenight. |
| Int% vs Double ratio config? | **Int percentages** | Consistent with PowerDevil patterns. Simpler QML. |
| Override model? | **Keybinds** (Alt+PgUp/PgDn) | Direct brightness adjustment via setBrightness(). Override persists until next scheduled update. |
| Pause toggle? | **v2 scope** | Inhibit mechanism + D-Bus method later |
| KCM placement? | **Night Light KCM** (plasma-workspace) | Users expect brightness scheduling alongside color temperature. UI placement follows user mental model, not code architecture. |
| isSupported()? | **Same as DimDisplay** | `core()->screenBrightnessController()->isSupported()`. Works for DDC/CI and backlights. |
| Naming? | **"Night Brightness"** | Mirrors "Night Color" (Night Light). Discoverable. |
| Plugin vs inline? | **New Action plugin** | Clean separation. Follows DimDisplay pattern. |
| Default enabled? | **false** (opt-in) | Not aggressive. User enables explicitly. |
| Per-profile vs global? | **Per-profile** | Consistent with existing patterns. Desktop has one profile (moot). |

## 7. Implementation: File Changes

### 7.1 New: `daemon/actions/bundled/nightbrightness.h`

```cpp
/*
 *   SPDX-FileCopyrightText: 2026 Sean Smith <DefendTheDisabled@gmail.com>
 *   SPDX-License-Identifier: GPL-2.0-or-later
 */
#pragma once

#include <powerdevilaction.h>
#include <QTimer>

class KDarkLightScheduleProvider;

namespace PowerDevil::BundledActions
{
class NightBrightness : public PowerDevil::Action
{
    Q_OBJECT
public:
    explicit NightBrightness(QObject *parent);

    bool loadAction(const PowerDevil::ProfileSettings &profileSettings) override;
    bool isSupported() override;

protected:
    void onProfileUnload() override;

private:
    void onScheduleChanged();
    void onDisplayAdded(const QString &displayId);
    void applyCurrentBrightness();
    void scheduleNextUpdate();
    double computeRatioForTime(const QDateTime &time) const;

    // Configuration (loaded from ProfileSettings)
    int m_bedtimePct = 40;
    int m_lateNightPct = 20;
    int m_bedtimeOffsetMin = 120;
    int m_lateNightOffsetMin = 180;
    int m_transitionMin = 30;

    // Schedule
    KDarkLightScheduleProvider *m_scheduleProvider = nullptr;
    QTimer m_updateTimer;

    // Inhibition
    PowerDevil::PolicyAgent::RequiredPolicies m_inhibitScreen = PowerDevil::PolicyAgent::None;

    static inline const QString DIMMING_ID = QStringLiteral("NightBrightness");
};
}
```

### 7.2 New: `daemon/actions/bundled/nightbrightness.cpp`

Core logic (~200 lines). Key methods:

**Constructor**: Initialize KDarkLightScheduleProvider with persisted state, connect signals, set policy.

**loadAction()**: Read config from ProfileSettings. If not enabled, restore ratio to 1.0 and return false. Otherwise connect displayAdded, connect scheduleChanged, call applyCurrentBrightness.

**isSupported()**: Same as DimDisplay — `core()->screenBrightnessController()->isSupported()`.

**computeRatioForTime()**: The curve function. Uses KDarkLightSchedule to get transition times, then:
1. Get previous and next transitions
2. Determine if daytime or nighttime
3. If nighttime, calculate position within sunset→bedtime→latenight curve
4. Return ratio (0.0-1.0)

**applyCurrentBrightness()**: Call `computeRatioForTime(now)`, then `setDimmingRatio(DIMMING_ID, ratio)`, then `scheduleNextUpdate()`.

**scheduleNextUpdate()**: Calculate next time ratio changes by ≥2%, set `m_updateTimer` to fire then. During transitions: ~180s intervals. During holds: sleep until next boundary.

**onProfileUnload()**: Restore ratio to 1.0.

### 7.3 New: `daemon/actions/bundled/powerdevilnightbrightnessaction.json`

```json
{
    "KPlugin": {
        "Description": "Adjusts display brightness based on time of day",
        "Icon": "brightness-auto",
        "Name": "Night Brightness"
    },
    "X-KDE-PowerDevil-Action-HasRuntimeRequirement": true,
    "X-KDE-PowerDevil-Action-ID": "NightBrightness"
}
```

### 7.4 Modified: `daemon/actions/bundled/CMakeLists.txt`

Add after `add_powerdevil_bundled_action(dimdisplay)`:

```cmake
add_powerdevil_bundled_action(nightbrightness)
target_link_libraries(powerdevil_nightbrightnessaction KNightTime)
```

### 7.5 Modified: Top-level `CMakeLists.txt`

Add after existing `find_package` block (~line 58):

```cmake
find_package(KNightTime REQUIRED)
```

### 7.6 Modified: `PowerDevilProfileSettings.kcfg`

Add after DimDisplay entries:

```xml
<entry name="NightBrightnessEnabled" type="Bool">
    <default>false</default>
</entry>
<entry name="NightBrightnessBedtimePct" type="Int">
    <default>40</default>
    <min>5</min><max>100</max>
</entry>
<entry name="NightBrightnessLateNightPct" type="Int">
    <default>20</default>
    <min>5</min><max>100</max>
</entry>
<entry name="NightBrightnessBedtimeOffsetMin" type="Int">
    <default>120</default>
    <min>0</min><max>720</max>
</entry>
<entry name="NightBrightnessLateNightOffsetMin" type="Int">
    <default>180</default>
    <min>0</min><max>720</max>
</entry>
<entry name="NightBrightnessTransitionMin" type="Int">
    <default>30</default>
    <min>1</min><max>120</max>
</entry>
```

Simple static defaults — no code-generated defaults needed (unlike DimDisplay which varies by profile/mobile).

### 7.7 Modified: `kcm/ui/ProfileConfig.qml`

Add after the dim display section (~line 449), before the "Turn off screen" section. Pattern follows existing dim display controls:

```qml
// Night Brightness section
Kirigami.Separator {
    visible: kcm.supportedActions["NightBrightness"] === true
    Kirigami.FormData.isSection: true
    Kirigami.FormData.label: i18nc("@title:group", "Night Brightness")
}

QQC2.CheckBox {
    id: nightBrightnessCheck
    visible: kcm.supportedActions["NightBrightness"] === true
    text: i18nc("@option:check", "Adjust brightness based on time of day")
    checked: profileSettings.nightBrightnessEnabled
    onToggled: profileSettings.nightBrightnessEnabled = checked
}

QQC2.Label {
    visible: nightBrightnessCheck.visible && nightBrightnessCheck.checked
    text: i18nc("@label", "Bedtime brightness: %1%", bedtimeSlider.value)
}
QQC2.Slider {
    id: bedtimeSlider
    visible: nightBrightnessCheck.visible && nightBrightnessCheck.checked
    from: 5; to: 100; stepSize: 5
    value: profileSettings.nightBrightnessBedtimePct
    onMoved: profileSettings.nightBrightnessBedtimePct = value
}

QQC2.Label {
    visible: nightBrightnessCheck.visible && nightBrightnessCheck.checked
    text: i18nc("@label", "Late night brightness: %1%", lateNightSlider.value)
}
QQC2.Slider {
    id: lateNightSlider
    visible: nightBrightnessCheck.visible && nightBrightnessCheck.checked
    from: 5; to: 100; stepSize: 5
    value: profileSettings.nightBrightnessLateNightPct
    onMoved: profileSettings.nightBrightnessLateNightPct = value
}
```

**Note**: The actual QML will be more polished — labels, layout, spinboxes for offset times. This sketch captures the pattern. Full QML follows the `TimeDurationComboBox` and `Kirigami.FormData` conventions visible in the existing ProfileConfig.qml.

## 8. Override & Inhibition

### Inhibition
Follow DimDisplay pattern: respect `PolicyAgent::ChangeScreenSettings`. If inhibited (presentation mode, video playback), set ratio to 1.0.

```cpp
connect(PolicyAgent::instance(), &PolicyAgent::unavailablePoliciesChanged,
        this, &NightBrightness::onUnavailablePoliciesChanged);

void NightBrightness::onUnavailablePoliciesChanged(PolicyAgent::RequiredPolicies policies) {
    m_inhibitScreen = policies & PolicyAgent::ChangeScreenSettings;
    if (m_inhibitScreen) {
        core()->screenBrightnessController()->setDimmingRatio(DIMMING_ID, 1.0);
    } else {
        applyCurrentBrightness();
    }
}
```

### Manual Override
**Option A (v1)**: No special override logic. `setDimmingRatio` is independent of `setBrightness`. User adjusts slider → absolute brightness changes. Our ratio still applies as multiplier. Clean separation.

### Pause Toggle (v2 scope)
Future: D-Bus method to temporarily set ratio to 1.0. System tray quick action.

## 9. Edge Cases

| Scenario | Handling |
|----------|----------|
| KNightTime unavailable | `isSupported()` still true (based on brightness controller, not schedule). If provider has no schedule, ratio stays at 1.0 (day). |
| Extreme latitude (no sunset) | `schedule.nextTransition()` returns `std::nullopt`. Hold current ratio. |
| DST / clock change | KDarkLightScheduleProvider handles internally. `scheduleChanged()` fires. |
| Monitor hotplug | Listen to `displayAdded`. Reapply current ratio. |
| Suspend/resume | QTimer fires on resume. `applyCurrentBrightness()` recalculates from current time. |
| Short summer night | Tiers that don't fit get clamped. If night < bedtimeOffset, jump directly to deepest applicable tier. |
| Profile switch | `loadAction()` called with new settings. Reapply immediately. |
| Inhibition active | Ratio set to 1.0. Restored when inhibition cleared. |
| bedtimeOffset > lateNightOffset | Validation: lateNightOffset must be > bedtimeOffset. If not, treat as equal (skip bedtime tier). |

## 10. Build & Deploy

### PKGBUILD Modifications

Clone Arch PKGBUILD (already at `~/ptime-build/powerdevil-pkg/`). Modifications:

1. Add `knighttime` to `depends=(...)`
2. Add patch application in `prepare()`:
   ```bash
   prepare() {
       cd $pkgname-$pkgver
       patch -Np1 -i "$srcdir/nightbrightness.patch"
   }
   ```
3. Add `source=(... nightbrightness.patch)` with `SKIP` checksum

### Patch Script

Python patch script (like our ptime patches) that:
1. Creates `nightbrightness.h`, `nightbrightness.cpp`, plugin JSON
2. Modifies CMakeLists.txt (adds `find_package(KNightTime)` and plugin registration)
3. Modifies `PowerDevilProfileSettings.kcfg` (adds config entries)
4. Modifies `kcm/ui/ProfileConfig.qml` (adds UI controls)

### Build & Install

```bash
cd ~/ptime-build/powerdevil-pkg
makepkg -sf    # -sf: force re-download source, rebuild
sudo pacman -U powerdevil-6.6.3-1-x86_64.pkg.tar.zst
```

### IgnorePkg

Add `powerdevil` to `IgnorePkg` in `/etc/pacman.conf` (alongside existing: coreutils, kio, rsync, tar, dolphin).

### Verification

After install and re-login:
1. Check plugin loaded: `journalctl --user -u plasma-powerdevil -b | grep NightBrightness`
2. Check config available: System Settings → Energy Saving → "Night Brightness" section
3. Enable and set bedtime=40%, late-night=20%
4. Verify brightness changes at expected times (or manually test by temporarily setting short offsets)

## 11. Prior Failures (Lessons Learned)

1. **clight**: Requires wlr-gamma-control. KDE Plasma doesn't expose it. Dead end.
2. **KWin Night Light patch (471 lines)**: Called PowerDevil D-Bus from KWin every 30s. I2C bus contention on NVIDIA → DDC/CI failures → brightness controls disappeared. Root: KWin is wrong component for DDC/CI. SDR brightness deliberately disabled (MR !455).
3. **Wrong D-Bus interface**: Used `setBrightnessSilent` (laptop API), then per-display `SetBrightness` at guessed paths. Frequency was the real problem.
4. **Key lesson**: `setDimmingRatio()` API was designed for exactly this use case. KNightTime was designed for exactly this scheduling need. Don't fight the architecture — use what KDE already built.

## 12. Upstream Submission Path

1. **Local deployment** — build patched PowerDevil, test on Praxis
2. **GitHub repo** — publish patch and documentation
3. **KDE Discuss** — post proposal, gather feedback
4. **bugs.kde.org** — file feature request with patch reference
5. **invent.kde.org MR** — formal merge request to `plasma/powerdevil`

Alignment with KDE direction: uses Jakob Petsovits' setDimmingRatio infrastructure (MR !361), respects MR !455's SDR brightness separation, uses Vlad Zahorodnii's KNightTime framework. Feature fills a documented gap (KDE Discuss threads from 2025 confirm user demand).
