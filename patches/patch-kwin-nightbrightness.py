#!/usr/bin/env python3
"""
Patch KWin Night Light to add software brightness scheduling via channel factors.
Extends Night Light with a 3-tier brightness curve (day/bedtime/late-night) that
multiplies RGB channel factors for software dimming — no DDC/CI, no EEPROM risk.

Supersedes the PowerDevil DDC/CI approach (patch-powerdevil-nightbrightness.py).

Usage: python3 patch-kwin-nightbrightness.py /path/to/kwin-6.6.3/
"""

import sys
import os

def patch_file(filepath, old, new):
    """Replace old text with new text in file."""
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  WARNING: Pattern not found in {filepath}")
        print(f"  Looking for: {old[:100]}...")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  Patched {filepath}")
    return True


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/kwin-source/")
        sys.exit(1)

    srcdir = sys.argv[1]
    if not os.path.isdir(srcdir):
        print(f"Error: {srcdir} is not a directory")
        sys.exit(1)

    nightlight_dir = os.path.join(srcdir, 'src', 'plugins', 'nightlight')
    if not os.path.exists(os.path.join(nightlight_dir, 'nightlightmanager.cpp')):
        print(f"Error: {srcdir} does not appear to be a KWin source tree")
        sys.exit(1)

    print("=== Patching KWin Night Light for Brightness Scheduling ===\n")

    # =========================================================================
    # 1. Patch constants.h — add brightness constants
    # =========================================================================
    print("[1/5] Patching constants.h")
    patch_file(
        os.path.join(nightlight_dir, 'constants.h'),
        'static const int MIN_TRANSITION_DURATION = 60000;\n\n}',
        '''static const int MIN_TRANSITION_DURATION = 60000;

// Brightness scheduling defaults
static const int DEFAULT_DAY_BRIGHTNESS_PCT = 100;
static const int DEFAULT_BEDTIME_BRIGHTNESS_PCT = 40;
static const int DEFAULT_LATENIGHT_BRIGHTNESS_PCT = 20;
static const int MIN_BRIGHTNESS_PCT = 10;
static const int DEFAULT_BEDTIME_OFFSET_MIN = 120;
static const int DEFAULT_LATENIGHT_OFFSET_MIN = 180;
static const int DEFAULT_BRIGHTNESS_TRANSITION_MIN = 30;

}'''
    )

    # =========================================================================
    # 2. Patch nightlightsettings.kcfg — add brightness config entries
    # =========================================================================
    print("[2/5] Patching nightlightsettings.kcfg")
    patch_file(
        os.path.join(nightlight_dir, 'nightlightsettings.kcfg'),
        '''        <entry name="NightTemperature" type="Int">
            <default>4500</default>
        </entry>
    </group>''',
        '''        <entry name="NightTemperature" type="Int">
            <default>4500</default>
        </entry>
        <entry name="NightBrightnessEnabled" type="Bool">
            <default>false</default>
        </entry>
        <entry name="NightBrightnessUseDdcCi" type="Bool">
            <default>false</default>
        </entry>
        <entry name="NightBrightnessDaytimePct" type="Int">
            <default>100</default>
            <min>10</min>
            <max>100</max>
        </entry>
        <entry name="NightBrightnessBedtimePct" type="Int">
            <default>40</default>
            <min>10</min>
            <max>100</max>
        </entry>
        <entry name="NightBrightnessLateNightPct" type="Int">
            <default>20</default>
            <min>10</min>
            <max>100</max>
        </entry>
        <entry name="NightBrightnessBedtimeOffsetMin" type="Int">
            <default>120</default>
            <min>0</min>
            <max>720</max>
        </entry>
        <entry name="NightBrightnessLateNightOffsetMin" type="Int">
            <default>180</default>
            <min>0</min>
            <max>720</max>
        </entry>
        <entry name="NightBrightnessTransitionMin" type="Int">
            <default>30</default>
            <min>1</min>
            <max>120</max>
        </entry>
    </group>'''
    )

    # =========================================================================
    # 3. Patch nightlightmanager.h — add brightness members and methods
    # =========================================================================
    print("[3/5] Patching nightlightmanager.h")

    # Add brightness method declarations in private section
    patch_file(
        os.path.join(nightlight_dir, 'nightlightmanager.h'),
        '    void commitGammaRamps(int temperature);',
        '''    void commitGammaRamps(int temperature);

    // Brightness scheduling
    double computeBrightnessRatio(const QDateTime &dateTime) const;
    void scheduleBrightnessUpdate();
    bool isDdcCiActiveForAnyOutput() const;
    void adjustBrightnessViaPowerDevil(int stepPercent);
    void onBrightnessKeyUp();
    void onBrightnessKeyDown();'''
    )

    # Add brightness member variables
    patch_file(
        os.path.join(nightlight_dir, 'nightlightmanager.h'),
        '    int m_inhibitReferenceCount = 0;\n    KConfigWatcher::Ptr m_configWatcher;',
        '''    int m_inhibitReferenceCount = 0;
    KConfigWatcher::Ptr m_configWatcher;

    // Brightness scheduling state
    double m_currentBrightnessRatio = 1.0;
    bool m_brightnessEnabled = false;
    bool m_brightnessUseDdcCi = false;
    int m_daytimeBrightnessPct = DEFAULT_DAY_BRIGHTNESS_PCT;
    int m_bedtimeBrightnessPct = DEFAULT_BEDTIME_BRIGHTNESS_PCT;
    int m_lateNightBrightnessPct = DEFAULT_LATENIGHT_BRIGHTNESS_PCT;
    int m_bedtimeOffsetMin = DEFAULT_BEDTIME_OFFSET_MIN;
    int m_lateNightOffsetMin = DEFAULT_LATENIGHT_OFFSET_MIN;
    int m_brightnessTransitionMin = DEFAULT_BRIGHTNESS_TRANSITION_MIN;
    std::unique_ptr<QTimer> m_brightnessTimer;
    bool m_brightnessManualOverride = false;'''
    )

    # =========================================================================
    # 4. Patch nightlightmanager.cpp — core implementation changes
    # =========================================================================
    print("[4/5] Patching nightlightmanager.cpp")

    cpp_path = os.path.join(nightlight_dir, 'nightlightmanager.cpp')

    # 4a. Add cmath include
    patch_file(
        cpp_path,
        '#include <QTimer>',
        '#include <QTimer>\n#include <cmath>'
    )

    # 4b. Add keybind setup in constructor — after existing toggle action block
    patch_file(
        cpp_path,
        '    connect(toggleAction, &QAction::triggered, this, &NightLightManager::toggle);',
        '''    connect(toggleAction, &QAction::triggered, this, &NightLightManager::toggle);

    // Brightness keybinds
    QAction *brightnessUpAction = new QAction(this);
    brightnessUpAction->setProperty("componentName", QStringLiteral("kwin"));
    brightnessUpAction->setObjectName(QStringLiteral("Night Brightness Up"));
    brightnessUpAction->setText(i18nc("Increase night brightness", "Increase Night Brightness"));
    KGlobalAccel::setGlobalShortcut(brightnessUpAction, QList<QKeySequence>{Qt::ALT | Qt::Key_PageUp});
    connect(brightnessUpAction, &QAction::triggered, this, &NightLightManager::onBrightnessKeyUp);

    QAction *brightnessDownAction = new QAction(this);
    brightnessDownAction->setProperty("componentName", QStringLiteral("kwin"));
    brightnessDownAction->setObjectName(QStringLiteral("Night Brightness Down"));
    brightnessDownAction->setText(i18nc("Decrease night brightness", "Decrease Night Brightness"));
    KGlobalAccel::setGlobalShortcut(brightnessDownAction, QList<QKeySequence>{Qt::ALT | Qt::Key_PageDown});
    connect(brightnessDownAction, &QAction::triggered, this, &NightLightManager::onBrightnessKeyDown);'''
    )

    # 4c. Patch hardReset() — compute brightness before commitGammaRamps
    patch_file(
        cpp_path,
        '''    if (isEnabled() && !isInhibited()) {
        setRunning(true);
        commitGammaRamps(currentTargetTemperature());
    }
    resetAllTimers();''',
        '''    if (isEnabled() && !isInhibited()) {
        setRunning(true);
        // Update brightness ratio before committing gamma ramps
        if (m_brightnessEnabled) {
            m_currentBrightnessRatio = computeBrightnessRatio(QDateTime::currentDateTime());
            m_brightnessManualOverride = false;
        } else {
            m_currentBrightnessRatio = 1.0;
        }
        commitGammaRamps(currentTargetTemperature());
    }
    resetAllTimers();'''
    )

    # 4d. Patch readConfig() — add brightness config reading
    patch_file(
        cpp_path,
        '    m_nightTargetTemperature = std::clamp(settings->nightTemperature(), MIN_TEMPERATURE, DEFAULT_DAY_TEMPERATURE);',
        '''    m_nightTargetTemperature = std::clamp(settings->nightTemperature(), MIN_TEMPERATURE, DEFAULT_DAY_TEMPERATURE);

    // Brightness scheduling config
    m_brightnessEnabled = settings->nightBrightnessEnabled();
    m_brightnessUseDdcCi = settings->nightBrightnessUseDdcCi();
    m_daytimeBrightnessPct = std::clamp(settings->nightBrightnessDaytimePct(), MIN_BRIGHTNESS_PCT, 100);
    m_bedtimeBrightnessPct = std::clamp(settings->nightBrightnessBedtimePct(), MIN_BRIGHTNESS_PCT, 100);
    m_lateNightBrightnessPct = std::clamp(settings->nightBrightnessLateNightPct(), MIN_BRIGHTNESS_PCT, 100);
    m_bedtimeOffsetMin = settings->nightBrightnessBedtimeOffsetMin();
    m_brightnessTransitionMin = settings->nightBrightnessTransitionMin();
    m_lateNightOffsetMin = std::max(settings->nightBrightnessLateNightOffsetMin(),
                                     m_bedtimeOffsetMin + m_brightnessTransitionMin);'''
    )

    # 4e. Patch cancelAllTimers() — add brightness timer reset
    patch_file(
        cpp_path,
        '''    m_slowUpdateStartTimer.reset();
    m_slowUpdateTimer.reset();
    m_quickAdjustTimer.reset();''',
        '''    m_slowUpdateStartTimer.reset();
    m_slowUpdateTimer.reset();
    m_quickAdjustTimer.reset();
    m_brightnessTimer.reset();'''
    )

    # 4f. Patch resetAllTimers() — add brightness scheduling at end
    patch_file(
        cpp_path,
        '''    updateTargetTemperature();
    resetQuickAdjustTimer(currentTargetTemperature());''',
        '''    updateTargetTemperature();
    resetQuickAdjustTimer(currentTargetTemperature());

    // Schedule brightness updates
    if (m_brightnessEnabled && m_active) {
        m_currentBrightnessRatio = computeBrightnessRatio(QDateTime::currentDateTime());
        scheduleBrightnessUpdate();
    }'''
    )

    # 4g. Replace commitGammaRamps() — multiply by brightness ratio
    patch_file(
        cpp_path,
        '''void NightLightManager::commitGammaRamps(int temperature)
{
    // TODO this list should ideally be filtered by workspace
    const QList<BackendOutput *> outputs = kwinApp()->outputBackend()->outputs();
    const QVector3D rgbFactors = sampleColorTemperature(temperature);
    for (BackendOutput *output : outputs) {
        output->setChannelFactors(rgbFactors);
    }

    setCurrentTemperature(temperature);
}''',
        '''void NightLightManager::commitGammaRamps(int temperature)
{
    // TODO this list should ideally be filtered by workspace
    const QList<BackendOutput *> outputs = kwinApp()->outputBackend()->outputs();
    const QVector3D rgbFactors = sampleColorTemperature(temperature);

    // Apply software brightness: multiply channel factors by brightness ratio
    const QVector3D adjustedFactors = (m_brightnessEnabled && m_currentBrightnessRatio < 1.0)
        ? rgbFactors * static_cast<float>(m_currentBrightnessRatio)
        : rgbFactors;

    for (BackendOutput *output : outputs) {
        output->setChannelFactors(adjustedFactors);
    }

    setCurrentTemperature(temperature);
}'''
    )

    # 4h. Add new methods before the closing namespace brace
    patch_file(
        cpp_path,
        '} // namespace KWin\n\n#include "moc_nightlightmanager.cpp"',
        BRIGHTNESS_METHODS + '\n} // namespace KWin\n\n#include "moc_nightlightmanager.cpp"'
    )

    # =========================================================================
    # 5. No CMakeLists.txt changes needed — no new files, no new dependencies
    # =========================================================================
    print("[5/5] No CMakeLists.txt changes needed")
    print("\n=== KWin Night Light brightness patch complete ===")
    print("Build with: makepkg -sf")
    print("Install with: sudo pacman -U kwin-*.pkg.tar.zst")


# New methods to append to nightlightmanager.cpp
BRIGHTNESS_METHODS = r'''
double NightLightManager::computeBrightnessRatio(const QDateTime &dateTime) const
{
    if (!m_brightnessEnabled || !m_active) {
        return 1.0;
    }

    const double daytimeRatio = m_daytimeBrightnessPct / 100.0;
    const double bedtimeRatio = m_bedtimeBrightnessPct / 100.0;
    const double lateNightRatio = m_lateNightBrightnessPct / 100.0;

    // Constant mode: always use bedtime brightness at night
    if (m_mode == NightLightMode::Constant) {
        return bedtimeRatio;
    }

    // Need scheduler for DarkLight mode
    if (!m_darkLightScheduler) {
        return daytimeRatio;
    }

    const auto prevTransition = m_darkLightScheduler->schedule().previousTransition(dateTime);
    const auto nextTransition = m_darkLightScheduler->schedule().nextTransition(dateTime);

    if (!prevTransition || !nextTransition) {
        return daytimeRatio; // Extreme latitude fallback
    }

    // Morning transition in progress (late-night → daytime)
    if (nextTransition->type() == KDarkLightTransition::Morning
        && nextTransition->test(dateTime) == KDarkLightTransition::InProgress) {
        const double progress = nextTransition->progress(dateTime);
        return std::lerp(lateNightRatio, daytimeRatio, progress);
    }

    // Evening transition in progress (daytime → bedtime)
    if (nextTransition->type() == KDarkLightTransition::Evening
        && nextTransition->test(dateTime) == KDarkLightTransition::InProgress) {
        const double progress = nextTransition->progress(dateTime);
        return std::lerp(daytimeRatio, bedtimeRatio, progress);
    }

    // Daytime — past morning, before evening
    if (m_daylight) {
        return daytimeRatio;
    }

    // Nighttime — compute position in bedtime/late-night curve
    QDateTime sunsetEnd;
    if (prevTransition->type() == KDarkLightTransition::Evening) {
        sunsetEnd = prevTransition->endDateTime();
    } else {
        return lateNightRatio; // Fallback
    }

    const qint64 secsSinceSunset = sunsetEnd.secsTo(dateTime);
    const qint64 bedtimeStartSecs = static_cast<qint64>(m_bedtimeOffsetMin) * 60;
    const qint64 transitionSecs = static_cast<qint64>(m_brightnessTransitionMin) * 60;

    if (secsSinceSunset < bedtimeStartSecs) {
        // Post-sunset hold at bedtime brightness
        return bedtimeRatio;
    }

    if (secsSinceSunset < bedtimeStartSecs + transitionSecs) {
        // Bedtime -> late-night transition
        const double progress = static_cast<double>(secsSinceSunset - bedtimeStartSecs)
                              / static_cast<double>(transitionSecs);
        return std::lerp(bedtimeRatio, lateNightRatio, std::clamp(progress, 0.0, 1.0));
    }

    // Late-night hold
    return lateNightRatio;
}

void NightLightManager::scheduleBrightnessUpdate()
{
    m_brightnessTimer.reset();

    if (!m_brightnessEnabled || !m_active || !m_running || m_brightnessManualOverride) {
        return;
    }

    const QDateTime now = QDateTime::currentDateTime();
    const double currentRatio = m_currentBrightnessRatio;

    // Scan forward to find when brightness changes by >= 1%
    const double threshold = 0.01;
    QDateTime nextChange;
    for (int secs = 30; secs <= 86400; secs += 30) {
        const QDateTime futureTime = now.addSecs(secs);
        const double futureRatio = computeBrightnessRatio(futureTime);
        if (std::abs(futureRatio - currentRatio) >= threshold) {
            nextChange = futureTime;
            break;
        }
    }

    if (!nextChange.isValid()) {
        return; // Brightness stable for 24h
    }

    int msToNext = static_cast<int>(now.msecsTo(nextChange));
    if (msToNext <= 0) {
        msToNext = 1000;
    }

    m_brightnessTimer = std::make_unique<QTimer>();
    m_brightnessTimer->setSingleShot(true);
    connect(m_brightnessTimer.get(), &QTimer::timeout, this, [this]() {
        m_currentBrightnessRatio = computeBrightnessRatio(QDateTime::currentDateTime());
        commitGammaRamps(m_currentTemperature);
        scheduleBrightnessUpdate();
    });
    m_brightnessTimer->start(msToNext);
}

bool NightLightManager::isDdcCiActiveForAnyOutput() const
{
    const auto outputs = kwinApp()->outputBackend()->outputs();
    for (BackendOutput *output : outputs) {
        if (output->brightnessDevice() && output->allowDdcCi()) {
            return true;
        }
    }
    return false;
}

void NightLightManager::adjustBrightnessViaPowerDevil(int stepPercent)
{
    const QString service = QStringLiteral("org.kde.Solid.PowerManagement");
    const QString path = QStringLiteral("/org/kde/Solid/PowerManagement/Actions/BrightnessControl");
    const QString iface = QStringLiteral("org.kde.Solid.PowerManagement.Actions.BrightnessControl");

    QDBusMessage getMax = QDBusMessage::createMethodCall(service, path, iface,
        QStringLiteral("brightnessMax"));
    QDBusReply<int> maxReply = QDBusConnection::sessionBus().call(getMax);

    QDBusMessage getCur = QDBusMessage::createMethodCall(service, path, iface,
        QStringLiteral("brightness"));
    QDBusReply<int> curReply = QDBusConnection::sessionBus().call(getCur);

    if (!maxReply.isValid() || !curReply.isValid() || maxReply.value() <= 0) {
        return;
    }

    const int bMax = maxReply.value();
    const int step = std::max(1, bMax * std::abs(stepPercent) / 100);
    int target;
    if (stepPercent > 0) {
        target = std::min(bMax, curReply.value() + step);
    } else {
        target = std::max(bMax * MIN_BRIGHTNESS_PCT / 100, curReply.value() - step);
    }

    QDBusMessage setMsg = QDBusMessage::createMethodCall(service, path, iface,
        QStringLiteral("setBrightness"));
    setMsg.setArguments({target});
    QDBusConnection::sessionBus().asyncCall(setMsg);
}

void NightLightManager::onBrightnessKeyUp()
{
    if (!m_brightnessEnabled || !m_active) {
        return;
    }
    if (isDdcCiActiveForAnyOutput()) {
        adjustBrightnessViaPowerDevil(5);
    } else {
        m_currentBrightnessRatio = std::min(1.0, m_currentBrightnessRatio + 0.05);
        m_brightnessManualOverride = true;
        commitGammaRamps(m_currentTemperature);
    }
}

void NightLightManager::onBrightnessKeyDown()
{
    if (!m_brightnessEnabled || !m_active) {
        return;
    }
    if (isDdcCiActiveForAnyOutput()) {
        adjustBrightnessViaPowerDevil(-5);
    } else {
        m_currentBrightnessRatio = std::max(static_cast<double>(MIN_BRIGHTNESS_PCT) / 100.0,
                                            m_currentBrightnessRatio - 0.05);
        m_brightnessManualOverride = true;
        commitGammaRamps(m_currentTemperature);
    }
}
'''


if __name__ == '__main__':
    main()
