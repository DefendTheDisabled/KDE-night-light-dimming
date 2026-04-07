# Change Proposal: NightBrightness v2 — Bug Fixes and Daytime Brightness

Author: Principal-Opus 2026-04-07T13:49
Edited by: Principal-Opus 2026-04-07T14:30 (post-audit fixes: B1-B4, W1-W10)
Edited by: Principal-Opus 2026-04-07T22:45 (edge case fixes: §4.6.1-4.6.4)
Edited by: Principal-Opus 2026-04-07T23:15 (boot delay eliminated, KCM apply fix: §4.7, D3/D6/D8/D9 revised)
Status: Final — ready for implementation

---

## 1. Problem Statement

Three bugs and one missing feature were discovered in the NightBrightness PowerDevil action (Phase 10, MRs plasma/powerdevil!623, plasma/plasma-workspace!6472) after submission:

| # | Severity | Summary |
|---|----------|---------|
| 1 | **Critical** | Daytime brightness never restored after night cycle — monitor stays at late-night level (20%) after reboot/login |
| 2 | **High** | Manual override (keybind) immediately overwritten — override flag cleared on next timer tick |
| 3 | **Low** | Dead code: `effectiveRatio()` declared in header, never implemented |
| 4 | **Feature gap** | No daytime brightness configuration — feature assumed all users want 100% during daytime |

All bugs were identified by 4 Associates (2x Opus, ChatGPT, Gemini) during structured review on 2026-04-07. Bug #1 was confirmed by PowerDevil journal log showing the exact failure path. Bug #4 was identified during fix design discussion — the user correctly pointed out that "daytime = 100%" is wrong; users set different brightness levels and forcing max is antithetical to KDE/Arch principles.

## 2. Root Cause Analysis

### Bug 1: Daytime Brightness Not Restored

**Proximate cause**: A post-submission fix added a guard in `applyCurrentBrightness()`:

```cpp
if (ratio >= 0.99) {
    qCInfo(POWERDEVIL) << "NightBrightness: daytime, not touching brightness";
    scheduleNextUpdate();
    return;
}
```

This prevents ALL daytime `setBrightness()` calls. When the system boots after a night where NightBrightness wrote 20% to DDC/CI, the monitor hardware retains 20%, the guard fires, and nothing ever restores daytime brightness. `scheduleNextUpdate()` then sleeps until sunset (hours away).

**Structural cause**: The guard conflates two concerns:
- Boot safety: DDC/CI may not be ready for writes during early init (real problem)
- Daytime semantics: ratio=1.0 means "full brightness" (NOT "no action needed")

**Underlying discovery**: PowerDevil does NOT persist external monitor brightness across sessions. `ScreenBrightnessControl` (the profile-based brightness action) requires a battery AND internal displays — `isSupported()` returns `false` on desktop PCs with external DDC/CI monitors. `m_rememberedDisplayState` in `ScreenBrightnessController` is session-only memory. On this system, NightBrightness is the ONLY entity writing DDC/CI brightness (besides user slider adjustments).

**Evidence** (PowerDevil journal, 2026-04-07):
```
10:02:07 NightBrightness: loaded — bedtime 40% at +120min, lateNight 20% at +180min, transition 30min
10:02:07 NightBrightness: daytime, not touching brightness
10:02:07 NightBrightness: next update in 34440 seconds
```

### Bug 2: Manual Override Immediately Cleared

```cpp
// In onIncreaseBrightness/onDecreaseBrightness:
m_manualOverrideRatio = -1.0;  // signal that user overrode

// In applyCurrentBrightness (called on next timer tick):
if (m_manualOverrideRatio.has_value()) {
    m_manualOverrideRatio.reset();  // immediately cleared
}
```

The override flag is set by keybind, then cleared unconditionally at the start of the next `applyCurrentBrightness()` call. The user gets at most one timer interval (~180 seconds during transitions) before NightBrightness overwrites their adjustment. The comment says "skip until next tier boundary" but the code does not implement that.

### Bug 3: Dead Code

`nightbrightness.h` declares `double effectiveRatio() const;` — no implementation exists in `.cpp`. Leftover from a prior design iteration. Not referenced, doesn't cause linker errors, but misleading.

### Feature Gap: No Daytime Brightness Config

The original design stated: "No configurable day brightness: Day is always 100% (ratio 1.0). Configuring day brightness would duplicate the existing brightness slider's function."

This is wrong. Users set different daytime brightness levels (some prefer 70%, some 50%) and forcing 100% every morning overrides their preference. Since PowerDevil doesn't persist external monitor brightness, NightBrightness must provide its own daytime brightness setting.

## 3. Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Add `NightBrightnessDaytimePct` config entry | Users need control over daytime brightness. Follows exact pattern of existing BedtimePct and LateNightPct. Default 100 preserves backward compatibility. |
| D2 | One-shot application at boot/sunrise, then dormant during daytime | NightBrightness applies brightness once on load, then `scheduleNextUpdate()` naturally sleeps until next transition (hours away during daytime). User adjustments via slider during the day persist. Thermostat-schedule mental model. |
| D3 | ~~Deferred DDC/CI write on ALL first-load paths~~ **REVISED D3**: Immediate write at boot, deferred only on resume | Codebase analysis (Associate-Opus) confirmed: `loadAction()` runs AFTER `detectionFinished()` — all display detection including DDC/CI probe is complete before NightBrightness loads. KDE's own `ScreenBrightnessControl` writes immediately at boot with zero delay. The `current == target` skip handles the original failure mode. Boot delay eliminated. Resume retains a 1.5s delay (DPMS wake may need DDC/CI reinitialization). |
| D4 | Explicit config over auto-capture | Two approaches were evaluated: (A) auto-capture pre-night brightness and restore it, (B) explicit daytime config. Option B chosen: simpler implementation, no state management, no capture-timing edge cases, matches existing config pattern, KDE philosophy of explicit user control. |
| D5 | Manual override persists until tier boundary | Override cleared only when scheduled ratio changes by more than KEYBIND_STEP (5%), indicating a tier boundary was crossed. During tier holds, user's override is preserved. |
| D6 | Suspend/resume triggers deferred re-apply (1.5s) | DPMS wake may require DDC/CI reinitialization. Resume handler schedules a 1.5s deferred write (shorter than the eliminated 5s boot delay). |
| D7 | Remove `ratio >= 0.99` guard entirely | The guard conflated timing safety with daytime semantics. With configurable DaytimePct, the guard is wrong — DaytimePct=70% produces ratio=0.7, bypassing the guard. |
| D8 | KCM config changes apply immediately | Night Light KCM `save()` overridden to call `org.kde.Solid.PowerManagement.refreshStatus()` D-Bus method, triggering PowerDevil to re-run `loadAction()`. Config reload applies brightness immediately (no deferred timer). |
| D9 | `m_firstLoadPending` eliminated from `loadAction()` | Boot writes are immediate (initialization sequence guarantees DDC/CI readiness). Config reload writes are immediate. Only `onPrepareForSleep()` uses deferred write. Renamed to `m_resumePending` for clarity. |

## 4. Fix Design

### 4.1 Daytime Brightness (Bugs #1 + #4)

**New config entry** in kwinrc `[NightColor]`:

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `NightBrightnessDaytimePct` | Int | 100 | 5-100 | Daytime brightness as percentage of monitor max |

**Modified `applyCurrentBrightness()`**:

The `ratio >= 0.99` guard is removed entirely. Boot and config-reload writes are immediate — `loadAction()` runs after `detectionFinished()`, so DDC/CI is confirmed ready. Only resume from suspend uses a deferred write (`m_resumePending`).

```cpp
void NightBrightness::applyCurrentBrightness()
{
    if (!m_loaded || m_inhibitScreen) return;

    // Manual override logic (see section 4.2)
    if (m_manualOverrideActive) {
        double currentRatio = computeRatioForTime(QDateTime::currentDateTime());
        if (std::abs(currentRatio - m_lastScheduledRatio) > KEYBIND_STEP) {
            m_manualOverrideActive = false;  // tier boundary crossed, clear override
        } else {
            scheduleNextUpdate();
            return;  // preserve user's manual adjustment
        }
    }

    const double ratio = computeRatioForTime(QDateTime::currentDateTime());
    m_lastScheduledRatio = ratio;

    // Resume from suspend: defer briefly for DDC/CI reinitialization
    if (m_resumePending) {
        m_resumePending = false;
        QTimer::singleShot(std::chrono::milliseconds(1500), this,
            [this, guard = QPointer(this)]() {
                if (!guard || !m_loaded || m_inhibitScreen) return;
                applyBrightnessNow();
            });
        qCInfo(POWERDEVIL) << "NightBrightness: resume, deferring"
                           << qRound(ratio * 100) << "% apply by 1.5s";
        scheduleNextUpdate();
        return;
    }

    // Boot, config reload, steady-state: apply immediately
    applyBrightnessNow();
    scheduleNextUpdate();
}
```

**Signature**: `void applyBrightnessNow(int retriesRemaining = 1);`

**Key change from v1**: `computeRatioForTime()` now returns `m_daytimePct / 100.0` for daytime periods (not 1.0). The `ratio >= 0.99` guard is gone. Daytime "one-shot then dormant" behavior is NATURAL — `scheduleNextUpdate()` scans ahead and finds no ratio change until sunset, so the timer sleeps for hours.

**The separate `applyDaytimeBrightness()` method from the earlier design is replaced by the generic `applyBrightnessNow()`** which handles both day and night by reading the current ratio. No day-specific logic needed.

**Key behaviors**:
- `m_firstLoadPending` set `true` in `loadAction()` (boot/config change)
- Set `true` on resume from suspend
- Cleared when `applyCurrentBrightness()` schedules the deferred timer
- `applyBrightnessNow()` handles the actual write — skips redundant writes (`current == target`), retries once if no displays registered
- Daytime "one-shot" is natural: after applying, `scheduleNextUpdate()` finds no ratio change until sunset → timer sleeps for hours

**`loadAction()` change** — add config read, NO deferred flag:
```cpp
m_daytimePct = group.readEntry("NightBrightnessDaytimePct", 100);
// No m_resumePending here — boot and config-reload writes are immediate.
// Only onPrepareForSleep() sets m_resumePending.
```

### 4.2 Manual Override Fix (Bug #2)

Replace `std::optional<double> m_manualOverrideRatio` with:

```cpp
bool m_manualOverrideActive = false;
double m_lastScheduledRatio = 1.0;
```

**Keybind handlers** set `m_manualOverrideActive = true`.

**`applyCurrentBrightness()`** checks: if override is active AND the scheduled ratio hasn't changed by more than KEYBIND_STEP (5%) since last application, skip the update. Override clears automatically when a tier boundary is crossed (ratio changes significantly).

**`loadAction()`** clears override: `m_manualOverrideActive = false`.

### 4.3 Dead Code Removal (Bug #3)

Remove `double effectiveRatio() const;` from `nightbrightness.h`.

### 4.4 Suspend/Resume Handler

Connect to system resume signal and reset the daytime restore flag:

```cpp
// In constructor — connect to Login1 PrepareForSleep:
QDBusConnection::systemBus().connect(
    u"org.freedesktop.login1"_s,
    u"/org/freedesktop/login1"_s,
    u"org.freedesktop.login1.Manager"_s,
    u"PrepareForSleep"_s,
    this, SLOT(onPrepareForSleep(bool)));

// NOTE: onPrepareForSleep MUST be declared as a public slot in the header
// (Q_SLOT or in public Q_SLOTS section) for the SLOT() macro to find it.

void NightBrightness::onPrepareForSleep(bool sleeping)
{
    if (!sleeping && m_loaded) {
        m_manualOverrideActive = false;  // pre-sleep override is stale (§4.6.3)
        m_resumePending = true;  // defer 1.5s for DDC/CI reinitialization
        applyCurrentBrightness();
    }
}
```

### 4.5 displayAdded Handler Fix

`onDisplayAdded()` currently calls `applyCurrentBrightness()` which hit the old daytime guard. With `computeRatioForTime()` now returning the correct ratio for ANY time period (daytimeRatio during day, bedtime/lateNight during night), the handler uses a single unified path — no day/night branching needed. `displayAdded` fires after DDC/CI probe succeeds, so no deferred timer is needed here (unlike first-load).

```cpp
void NightBrightness::onDisplayAdded(const QString &displayId)
{
    if (!m_loaded || m_inhibitScreen) return;

    const double ratio = computeRatioForTime(QDateTime::currentDateTime());
    auto *ctrl = core()->screenBrightnessController();
    int maxB = ctrl->maxBrightness(displayId);
    int target = qRound(maxB * ratio);
    target = std::clamp(target, ctrl->minBrightness(displayId), maxB);
    int current = ctrl->brightness(displayId);
    if (current != target) {
        ctrl->setBrightness(displayId, target, SOURCE_NAME, u"hotplug"_s,
                           ScreenBrightnessController::SuppressIndicator);
    }
}
```

### 4.6 Edge Case Fixes (Post-Audit)

Four edge cases identified during Associate audit and Principal validation. All are solvable with minimal code (~18 lines total).

#### 4.6.1 Short-Night Sunrise Discontinuity

**Problem**: Morning transition always lerps from `lateNightRatio`, but if sunrise starts during the bedtime hold (high-latitude short summer nights), brightness jumps from `bedtimeRatio` to `lateNightRatio` at sunrise start.

**Fix**: In `computeRatioForTime()`, compute the actual pre-sunrise nighttime ratio instead of assuming `lateNightRatio`:

```cpp
// In the morning transition block, replace:
//   return std::lerp(lateNightRatio, daytimeRatio, progress);
// With:

const QDateTime sunriseStart = nextTransition->startDateTime();
const qint64 nightDuration = prevTransition->endDateTime().secsTo(sunriseStart);
const qint64 bedtimeStartSecs = m_bedtimeOffsetMin * 60;
const qint64 lateTransitionStart = m_lateNightOffsetMin * 60 - m_transitionMin * 60;
const qint64 lateNightStartSecs = m_lateNightOffsetMin * 60;

double preSunriseRatio;
if (nightDuration < bedtimeStartSecs) {
    preSunriseRatio = bedtimeRatio;  // never left post-sunset hold
} else if (nightDuration < lateTransitionStart) {
    preSunriseRatio = bedtimeRatio;  // in extended bedtime hold
} else if (nightDuration < lateNightStartSecs) {
    double p = double(nightDuration - lateTransitionStart) / double(m_transitionMin * 60);
    preSunriseRatio = std::lerp(bedtimeRatio, lateNightRatio, std::clamp(p, 0.0, 1.0));
} else {
    preSunriseRatio = lateNightRatio;  // normal case
}
return std::lerp(preSunriseRatio, daytimeRatio, progress);
```

#### 4.6.2 Keybind During Boot Delay Override

**Problem**: If user presses a keybind during the 5-second deferred boot delay, the deferred timer fires and overwrites their adjustment.

**Fix**: 1 line at top of `applyBrightnessNow()`:

```cpp
if (m_manualOverrideActive) return;  // user took control during delay, respect it
```

#### 4.6.3 Override Stale After Resume

**Problem**: If manual override was active before suspend, resume path checks override first, returns early, and the deferred write never schedules.

**Fix**: 1 line in `onPrepareForSleep()`:

```cpp
void NightBrightness::onPrepareForSleep(bool sleeping)
{
    if (!sleeping && m_loaded) {
        m_manualOverrideActive = false;  // pre-sleep override is stale
        m_firstLoadPending = true;
        applyCurrentBrightness();
    }
}
```

#### 4.6.4 `lateNightOffsetMin` Config Ignored (Pre-existing Bug)

**Problem**: User configures "Late night starts: 3h after sunset" but the code uses `bedtimeOffsetMin + transitionMin` as the late-night boundary (2h30m). The `lateNightOffsetMin` value is read, validated, and displayed in the UI, but never used in the ratio calculation. The compiler warning about unused `lateNightStartSecs` is a symptom.

**Fix**: In the nighttime tier logic of `computeRatioForTime()`, use `lateNightStartSecs` properly:

```cpp
// Replace the current Phase 1/2/3 nighttime logic with:

// Phase 1: Post-sunset hold at bedtime level
if (secsSinceSunset < bedtimeStartSecs) {
    return bedtimeRatio;
}

// Phase 1.5: Extended bedtime hold (gap between bedtime and late-night transition)
const qint64 lateTransitionStart = lateNightStartSecs - transitionSecs;
if (secsSinceSunset < lateTransitionStart) {
    return bedtimeRatio;
}

// Phase 2: Bedtime → late-night transition
if (secsSinceSunset < lateNightStartSecs) {
    const double progress = double(secsSinceSunset - lateTransitionStart) / double(transitionSecs);
    return std::lerp(bedtimeRatio, lateNightRatio, std::clamp(progress, 0.0, 1.0));
}

// Phase 3: Late-night hold
return lateNightRatio;
```

This correctly implements: bedtime hold until `lateNightOffsetMin - transitionMin`, then transition for `transitionMin` minutes, arriving at late-night at exactly `lateNightOffsetMin` after sunset. Also eliminates the unused-variable compiler warning.

### 4.7 KCM Config Apply Triggers Immediate Brightness Update (D8)

**Problem**: Changing daytime brightness in Night Light settings and clicking Apply writes to kwinrc but doesn't change monitor brightness until next login.

**Root cause**: Night Light KCM writes config via `KQuickManagedConfigModule::save()` but sends no notification to PowerDevil. NightBrightness only reads config in `loadAction()`, which only runs on profile load — not config file changes.

**Fix**: Override `save()` in the Night Light KCM to call PowerDevil's `refreshStatus()` D-Bus method after saving. This is the same pattern PowerDevil's own KCM (`PowerKCM::save()`) uses.

In `plasma-workspace/kcms/nightlight/kcm.h`:
```cpp
void save() override;
```

In `plasma-workspace/kcms/nightlight/kcm.cpp`:
```cpp
void KCMNightLight::save()
{
    KQuickManagedConfigModule::save();

    // Notify PowerDevil to reload NightBrightness config immediately
    auto call = QDBusMessage::createMethodCall(
        u"org.kde.Solid.PowerManagement"_s,
        u"/org/kde/Solid/PowerManagement"_s,
        u"org.kde.Solid.PowerManagement"_s,
        u"refreshStatus"_s);
    QDBusConnection::sessionBus().asyncCall(call);
}
```

**Requires**: `#include <QDBusMessage>` and `#include <QDBusConnection>` in kcm.cpp.

**Effect**: After Apply, PowerDevil calls `loadProfile(true)` → `loadAllInactiveActions()` → `NightBrightness::loadAction()` reads fresh config → `applyCurrentBrightness()` writes brightness immediately (no deferred timer — config reload is not a boot or resume event).

## 5. UI Changes (plasma-workspace)

Add daytime brightness slider to Night Light KCM (`main.qml`), positioned before the existing bedtime slider:

```qml
GridLayout {
    Kirigami.FormData.label: i18nc("@label:slider", "Daytime brightness:")
    Kirigami.FormData.buddyFor: daytimeSlider
    enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
    columns: 4

    QQC2.Slider {
        id: daytimeSlider
        Layout.minimumWidth: modeSwitcher.width
        Layout.columnSpan: 3
        from: 100; to: 5; stepSize: -5; live: true
        value: kcm.nightLightSettings.nightBrightnessDaytimePct
        onMoved: kcm.nightLightSettings.nightBrightnessDaytimePct = value

        KCM.SettingStateBinding {
            configObject: kcm.nightLightSettings
            settingName: "NightBrightnessDaytimePct"
            extraEnabledConditions: kcm.nightLightSettings.active
                && kcm.nightLightSettings.nightBrightnessEnabled
        }
    }
    QQC2.Label {
        text: i18nc("Brightness percentage", "%1%", daytimeSlider.value)
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignRight
        Layout.minimumWidth: sliderValueLabelMetrics.implicitWidth
    }
    QQC2.Label { text: i18nc("@info:label Slider end label for maximum brightness", "Bright") }
    Item { Layout.fillWidth: true }
    QQC2.Label { text: i18nc("@info:label Slider end label for minimum brightness", "Dim") }
    Item {}
}
```

Add kcfg entry to `nightlightsettings.kcfg`:
```xml
<entry name="NightBrightnessDaytimePct" type="Int">
    <default>100</default>
    <min>5</min><max>100</max>
</entry>
```

## 6. Updated Brightness Curve Model

Previous model was 3-tier (day fixed at 100%, bedtime, late-night). Now 4-tier with configurable day:

```
Ratio
 daytime ┤━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
         │           DAY              ┃╲  sunset
         │                            ┃  ╲ transition
bedtime  ┤                            ┃    ━━━━━━━━━━━━┓ BEDTIME
         │                            ┃                 ┃╲
latenight┤                            ┃                 ┃  ━━━━━━ LATE NIGHT
         │                            ┃                 ┃       ╱
         ├────────────┼──────────────┼─────────┼───────┼─────┼──
       sunrise     sunset        sunset+2h sunset+3h  sunrise
```

All three levels (daytime, bedtime, late-night) are user-configurable percentages.

`computeRatioForTime()` change: where it previously returned `1.0` for daytime, it now returns `m_daytimePct / 100.0`. The evening transition lerps from `daytimeRatio` to `bedtimeRatio` (not from 1.0).

## 7. Files Changed

### PowerDevil (daemon)
| File | Change |
|------|--------|
| `nightbrightness.h` | Add `m_daytimePct`, `m_resumePending` (replaces `m_firstLoadPending`), `m_manualOverrideActive`, `m_lastScheduledRatio`. Remove `effectiveRatio()`, `m_manualOverrideRatio`. Add `void applyBrightnessNow(int retriesRemaining = 1)` (private). Add `public Q_SLOTS: void onPrepareForSleep(bool sleeping)` — MUST be a slot for SLOT() macro D-Bus connection. |
| `nightbrightness.cpp` | Remove `ratio >= 0.99` guard. Rewrite `applyCurrentBrightness()` per §4.1 — boot/config writes immediate, only resume deferred (1.5s). Fix override logic per §4.2. Add `applyBrightnessNow()`, `onPrepareForSleep()`. Update `computeRatioForTime()`: use `m_daytimePct` for daytime, fix nighttime tier logic to use `lateNightStartSecs` (§4.6.4), fix morning transition to compute actual pre-sunrise ratio (§4.6.1). Add override check in `applyBrightnessNow()` (§4.6.2). Clear override in `onPrepareForSleep()` (§4.6.3). Update `loadAction()` to read `DaytimePct` — NO deferred flag set. Fix `onDisplayAdded()` per §4.5. |

### plasma-workspace (KCM)
| File | Change |
|------|--------|
| `nightlightsettings.kcfg` | Add `NightBrightnessDaytimePct` entry |
| `ui/main.qml` | Add daytime brightness slider before bedtime slider |
| `kcm.h` | Add `void save() override;` declaration |
| `kcm.cpp` | Add `save()` implementation calling PowerDevil `refreshStatus()` D-Bus method (§4.7). Add `#include <QDBusMessage>` and `#include <QDBusConnection>`. |

## 8. Testing Requirements

| Scenario | Expected Behavior |
|----------|-------------------|
| Boot during daytime after nighttime shutdown | Daytime brightness applied immediately (no delay) |
| Boot during nighttime | Night tier brightness applied immediately (no delay) |
| Normal sunset transition | Daytime → bedtime transition at configured rate |
| Normal sunrise transition | Late-night → daytime transition at configured rate |
| Suspend during night → resume during day | Daytime brightness applied after ~1.5s delay |
| Change DaytimePct in Night Light KCM → Apply | Monitor brightness changes immediately |
| Change BedtimePct in KCM → Apply (during night) | Night brightness updates immediately |
| User adjusts slider during daytime | Adjustment persists; no override until next sunrise |
| User adjusts slider during nighttime (keybind) | Override persists until next tier boundary |
| Monitor hotplug during daytime | New monitor gets daytime brightness |
| Monitor hotplug during nighttime | New monitor gets current night tier brightness |
| Feature disabled | No brightness changes; user controls slider freely |
| DaytimePct set to 70% | Morning restore writes 70%, not 100% |
| DaytimePct set to 100% (default) | Backward-compatible with pre-fix behavior (but now actually works) |
| Inhibition active (presentation mode) | No brightness changes |
| Profile switch (AC↔Battery) | Deferred apply does not re-fire (flag already cleared) |
| DaytimePct < BedtimePct (inverted ordering) | Brightness increases at sunset — counterintuitive but user chose it. No crash. Document in UI tooltip. |
| Keybind during 5-second boot delay | Deferred timer respects override — user's adjustment preserved (§4.6.2) |
| Suspend with override active → resume | Override cleared on resume, deferred write schedules correctly (§4.6.3) |
| Short summer night (sunrise during bedtime hold) | Morning transition lerps from bedtimeRatio, not lateNightRatio — no discontinuity (§4.6.1) |
| LateNightOffsetMin > BedtimeOffsetMin + TransitionMin | Extended bedtime hold fills the gap; late-night starts at configured time (§4.6.4) |

### Known Limitations (document in SPEC)
- **Manual override only covers NightBrightness keybinds**, not the KDE system tray brightness slider. A user dragging the slider at night will still be overwritten on the next scheduled tick. Full slider integration requires connecting to `ScreenBrightnessController::brightnessChanged` with source filtering — deferred to v3.
- **Override clearing is 5% drift, not strict tier boundary**. During gradual transitions, override clears after the scheduled ratio drifts >5% from the override point (~6-9 minutes into a transition). Acceptable for v2; strict phase-based clearing deferred to v3.

## 9. Risk Assessment

| Risk | Mitigation |
|------|------------|
| DDC/CI boot failure (original bug) | Eliminated: `loadAction()` runs after `detectionFinished()` — DDC/CI confirmed ready. `current == target` skip prevents the specific 100%-write failure. ScreenBrightnessControl already writes immediately at boot. If failure reappears on testing, reintroduce 1-2s delay. |
| User slider fight during daytime | One-shot application — NightBrightness applies daytime once then backs off until evening |
| Timer fires on deleted object | QPointer guard in lambda capture |
| Night falls during 5s delay | `applyBrightnessNow()` re-computes current ratio and applies correct brightness for current period — even if the period changed during the delay |
| No displays at deferred write time | Single retry after 5 more seconds |
| Keybind during boot delay | `applyBrightnessNow()` checks `m_manualOverrideActive` — respects user's adjustment |
| Override stale after resume | `onPrepareForSleep()` clears override before scheduling deferred write |
| Short-night sunrise brightness jump | Morning transition computes actual pre-sunrise ratio, not assumed lateNightRatio |
