# KDE Night Light Dimming

Automatic brightness scheduling for KDE Plasma, integrated into the Night Light settings page. Dims your display based on sunrise/sunset — the brightness counterpart to Night Light's color temperature shift.

Supports two brightness paths: **software brightness** via KWin's compositor pipeline (default) and **DDC/CI hardware brightness** when enabled in Display Configuration.

![Night Light settings with brightness controls](https://github.com/DefendTheDisabled/KDE-night-light-dimming/blob/main/screenshots/night-light-settings.png?raw=true)

## What It Does

Night Light already shifts color temperature at sunset. This adds **brightness dimming** to the same schedule:

- **Daytime**: Configurable brightness (default 100%) — applied at login and sunrise
- **Bedtime** (configurable, default 2h after sunset): Reduced brightness (default 40%)
- **Late night** (configurable, default 3h after sunset): Low brightness (default 20%)
- **Sunrise**: Gradual return to daytime brightness

Transitions are smooth — brightness changes in small steps over configurable durations (default 30 minutes per transition).

### Manual Override

**Alt+PgUp / Alt+PgDn** adjust brightness at any time. Shortcuts are configurable directly in the Night Light settings page. When DDC/CI is enabled in Display Configuration, keybinds adjust hardware brightness; when disabled, they adjust software brightness.

## How It Works

Patches to two KDE packages:

| Package | What Changes | Purpose |
|---------|-------------|---------|
| **kwin** | Night Light plugin brightness scheduling | Daemon: multiplies channel factors by brightness ratio in `commitGammaRamps()` |
| **plasma-workspace** | Night Light KCM additions | UI: brightness controls + inline shortcut editing in Night Light settings page |

### Architecture

```
KWin Night Light Plugin (nightlight.so)
  NightLightManager
  ├─ Color temperature scheduling              ← existing
  ├─ Brightness scheduling                     ← new
  │   ├─ computeBrightnessRatio() → 3-tier curve
  │   ├─ m_currentBrightnessRatio multiplied into commitGammaRamps()
  │   └─ scheduleBrightnessUpdate() → dedicated brightness timer
  ├─ KDarkLightScheduleProvider                ← existing (shared)
  ├─ DDC/CI-aware keybinds                     ← new
  │   ├─ allowDdcCi() = true → PowerDevil D-Bus setBrightness()
  │   └─ allowDdcCi() = false → channel factor adjustment
  └─ Config: kwinrc [NightColor]               ← existing group, new entries

plasma-workspace (Night Light KCM)
  ├─ Brightness controls (sliders, spinboxes, checkbox)
  ├─ Inline KeySequenceItem shortcut editing
  └─ Config bindings to kwinrc [NightColor]
```

### Software vs DDC/CI Brightness

| | Software (Channel Factors) | DDC/CI (Hardware) |
|-|---------------------------|-------------------|
| **How** | Multiplies RGB output in compositor | Writes VCP 0x10 to monitor |
| **Latency** | Instant (next frame) | ~1 second (I2C bus) |
| **Works on** | All DRM-backed displays | DDC/CI capable monitors only |
| **Contrast** | Slightly reduced (blacks unchanged) | Preserved (backlight dimmed) |
| **Hardware writes** | None | Yes (monitor EEPROM/flash) |
| **Default** | Yes | When user enables DDC/CI |

**On DDC/CI EEPROM wear**: We researched this concern extensively and found zero documented cases of monitor failure from DDC/CI brightness automation across major tool communities (ddcutil, MonitorControl, Lunar, Twinkle Tray — combined hundreds of thousands of users over years). The Lunar developer tested millions of writes over 5 years without storage failure. We consider this a precautionary hypothesis. If evidence exists showing DDC/CI brightness writes cause monitor degradation, we welcome review of that data.

## Compatibility

- **KDE Plasma**: 6.6.x (tested on 6.6.3)
- **Distribution**: Build instructions for Arch Linux / EndeavourOS
- **GPU**: Tested with NVIDIA RTX 5060 Ti (proprietary driver). Night Light channel factors work via existing Night Light code path.
- **Monitor**: Software brightness works on all displays. DDC/CI path requires DDC/CI capable monitor.

## Installation (Arch Linux / EndeavourOS)

### Prerequisites

- KDE Plasma 6.6+
- Python 3
- For DDC/CI: `ddcutil detect` shows your monitor

### Build and Install

**KWin (brightness scheduling):**

```bash
# Get the PKGBUILD
pkgctl repo clone --protocol=https kwin
cd kwin

# Add to PKGBUILD prepare():
#   prepare() {
#     cd $pkgname-$pkgver
#     patch -Np1 -i /path/to/kwin-nightbrightness.patch
#   }

makepkg -sf --skippgpcheck
sudo pacman -U kwin-*.pkg.tar.zst
```

**Night Light KCM (UI):**

```bash
# Get the PKGBUILD
pkgctl repo clone --protocol=https plasma-workspace
cd plasma-workspace

# Download and extract source
makepkg -o --skippgpcheck

# Apply patch
patch -Np1 -d src/plasma-workspace-*/ < /path/to/plasma-workspace-nightbrightness.patch

# Configure and build only the KCM target
cmake -B build -S src/plasma-workspace-*/ -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_TESTING=OFF
cmake --build build --target kcm_nightlight

# Install the single .so
sudo cp build/bin/plasma/kcms/systemsettings/kcm_nightlight.so \
        /usr/lib/qt6/plugins/plasma/kcms/systemsettings/kcm_nightlight.so
```

**After installation:** Log out and back in. Open System Settings → Display & Monitor → Night Light.

### Protecting from Updates

```bash
# Add kwin to IgnorePkg in /etc/pacman.conf
# plasma-workspace: re-run KCM build after pacman -Syu updates it
```

## Configuration

All settings in `~/.config/kwinrc` under `[NightColor]`:

| Setting | Default | Description |
|---------|---------|-------------|
| `NightBrightnessEnabled` | false | Master enable |
| `NightBrightnessDaytimePct` | 100 | Daytime brightness (%) |
| `NightBrightnessBedtimePct` | 40 | Bedtime brightness (%) |
| `NightBrightnessLateNightPct` | 20 | Late night brightness (%) |
| `NightBrightnessBedtimeOffsetMin` | 120 | Minutes after sunset for bedtime |
| `NightBrightnessLateNightOffsetMin` | 180 | Minutes after sunset for late night |
| `NightBrightnessTransitionMin` | 30 | Transition duration (minutes) |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Alt+PgUp | Increase brightness (5% steps) |
| Alt+PgDn | Decrease brightness (5% steps) |

Configurable directly in Night Light settings page or System Settings → Shortcuts → Window Management.

## Technical Notes

### Why Channel Factors?

Night Light already uses `setChannelFactors()` to apply color temperature shifts through KWin's color-managed compositor pipeline. Brightness dimming uses the same mechanism — multiplying all three RGB channels uniformly reduces the output white point's luminance via `applyNightLight()` → `dimmed(newWhite.Y)`.

This path is completely independent of the brightness device / `allowSdrSoftwareBrightness` system that [MR !455](https://invent.kde.org/plasma/powerdevil/-/merge_requests/455) disabled for SDR external monitors.

### Files Modified

**kwin** (4 files):
- `src/plugins/nightlight/constants.h` — brightness defaults
- `src/plugins/nightlight/nightlightsettings.kcfg` — 7 config entries
- `src/plugins/nightlight/nightlightmanager.h` — brightness state and methods
- `src/plugins/nightlight/nightlightmanager.cpp` — brightness scheduling, channel factor multiplication, DDC/CI-aware keybinds

**plasma-workspace** (5 files):
- `kcms/nightlight/CMakeLists.txt` — no new dependencies
- `kcms/nightlight/nightlightsettings.kcfg` — 7 config entries
- `kcms/nightlight/kcm.h` — shortcut read/write methods
- `kcms/nightlight/kcm.cpp` — KSharedConfig-based shortcut management
- `kcms/nightlight/ui/main.qml` — brightness controls + KeySequenceItem shortcut editing

## Merge Requests

- **KWin**: [plasma/kwin!XXX](https://invent.kde.org/plasma/kwin/-/merge_requests/XXX) (Night Light brightness scheduling)
- **plasma-workspace**: [plasma/plasma-workspace!6472](https://invent.kde.org/plasma/plasma-workspace/-/merge_requests/6472) (Night Light KCM UI)
- **PowerDevil** (closed): [plasma/powerdevil!623](https://invent.kde.org/plasma/powerdevil/-/merge_requests/623) — original DDC/CI-only approach, superseded by dual-path KWin implementation

## License

GPL-2.0-or-later — matching KWin and plasma-workspace.

## Author

Sean Smith (DefendTheDisabled) — developed with AI agent assistance (OpenCode ACF multi-agent framework).
