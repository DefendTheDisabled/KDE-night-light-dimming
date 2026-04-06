#!/usr/bin/env python3
"""
Patch PowerDevil to add NightBrightness action.
Adds time-based DDC/CI brightness scheduling using setDimmingRatio() + KNightTime.

Usage: python3 patch-powerdevil-nightbrightness.py /path/to/powerdevil-6.6.3/
"""

import sys
import os

def patch_file(filepath, old, new):
    """Replace old text with new text in file."""
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  WARNING: Pattern not found in {filepath}")
        print(f"  Looking for: {old[:80]}...")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    return True

def write_file(filepath, content):
    """Write a new file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"  Created {filepath}")

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/powerdevil-source/")
        sys.exit(1)

    srcdir = sys.argv[1]
    if not os.path.isdir(srcdir):
        print(f"Error: {srcdir} is not a directory")
        sys.exit(1)

    # Verify this is a PowerDevil source tree
    if not os.path.exists(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'dimdisplay.cpp')):
        print(f"Error: {srcdir} does not appear to be a PowerDevil source tree")
        sys.exit(1)

    print("=== Patching PowerDevil for NightBrightness ===\n")

    # 1. Create nightbrightness.h
    print("[1/7] Creating nightbrightness.h")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'nightbrightness.h'), NIGHTBRIGHTNESS_H)

    # 2. Create nightbrightness.cpp
    print("[2/7] Creating nightbrightness.cpp")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'nightbrightness.cpp'), NIGHTBRIGHTNESS_CPP)

    # 3. Create plugin JSON
    print("[3/7] Creating plugin JSON")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'powerdevilnightbrightnessaction.json'), PLUGIN_JSON)

    # 4. Patch daemon/actions/bundled/CMakeLists.txt
    print("[4/7] Patching actions CMakeLists.txt")
    ok = patch_file(
        os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'CMakeLists.txt'),
        'add_powerdevil_bundled_action(dimdisplay)',
        'add_powerdevil_bundled_action(dimdisplay)\n\nadd_powerdevil_bundled_action(nightbrightness)\ntarget_link_libraries(powerdevil_nightbrightnessaction KNightTime KF6::GlobalAccel KF6::XmlGui)'
    )
    if ok:
        print("  OK")

    # 5. Patch top-level CMakeLists.txt — add find_package(KNightTime)
    print("[5/7] Patching top-level CMakeLists.txt")
    ok = patch_file(
        os.path.join(srcdir, 'CMakeLists.txt'),
        'find_package(DDCUtil)',
        'find_package(KNightTime REQUIRED)\nfind_package(DDCUtil)'
    )
    if ok:
        print("  OK")

    # Steps 6-7 removed: config and UI now live in Night Light KCM (plasma-workspace)
    # Use patch-plasma-nightbrightness.py for the UI side

    print("\n=== Patching complete (daemon only — UI is in plasma-workspace) ===")
    print("Build with: cmake -B build -S . -DCMAKE_INSTALL_LIBEXECDIR=lib -DBUILD_TESTING=OFF && cmake --build build")


# ============================================================
# File contents
# ============================================================

NIGHTBRIGHTNESS_H = r'''/*
 *   SPDX-FileCopyrightText: 2026 Sean Smith <DefendTheDisabled@gmail.com>
 *
 *   SPDX-License-Identifier: GPL-2.0-or-later
 */

#pragma once

#include <powerdevilaction.h>

#include <QDateTime>
#include <QTimer>
#include <optional>

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

private Q_SLOTS:
    void onScheduleChanged();
    void onUnavailablePoliciesChanged(PowerDevil::PolicyAgent::RequiredPolicies policies);
    void onDisplayAdded(const QString &displayId);
    void onIncreaseBrightness();
    void onDecreaseBrightness();

private:
    void ensureScheduleProvider();
    void applyCurrentBrightness();
    void scheduleNextUpdate();
    double computeRatioForTime(const QDateTime &time) const;
    double effectiveRatio() const;

    // Configuration
    int m_bedtimePct = 40;
    int m_lateNightPct = 20;
    int m_bedtimeOffsetMin = 120;
    int m_lateNightOffsetMin = 180;
    int m_transitionMin = 30;
    bool m_loaded = false;

    // Schedule — lazily initialized in loadAction, NOT constructor
    KDarkLightScheduleProvider *m_scheduleProvider = nullptr;
    QTimer m_updateTimer;

    // Manual override (keybinds)
    std::optional<double> m_manualOverrideRatio;

    // Inhibition
    PowerDevil::PolicyAgent::RequiredPolicies m_inhibitScreen = PowerDevil::PolicyAgent::None;
};

} // namespace PowerDevil::BundledActions
'''

NIGHTBRIGHTNESS_CPP = r'''/*
 *   SPDX-FileCopyrightText: 2026 Sean Smith <DefendTheDisabled@gmail.com>
 *
 *   SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "nightbrightness.h"

#include <PowerDevilProfileSettings.h>
#include <powerdevil_debug.h>
#include <powerdevilcore.h>

#include <KDarkLightScheduleProvider>
#include <KDarkLightSchedule>
#include <KSharedConfig>
#include <KActionCollection>
#include <KGlobalAccel>

#include <KLocalizedString>
#include <KPluginFactory>

#include <QAction>
#include <QDateTime>
#include <algorithm>
#include <cmath>

K_PLUGIN_CLASS_WITH_JSON(PowerDevil::BundledActions::NightBrightness, "powerdevilnightbrightnessaction.json")

using namespace Qt::Literals::StringLiterals;

static constexpr double KEYBIND_STEP = 0.05; // 5% per keypress
static const QString SOURCE_NAME = u"NightBrightness"_s;

namespace PowerDevil::BundledActions
{

NightBrightness::NightBrightness(QObject *parent)
    : Action(parent)
{
    // Minimal constructor — follows DimDisplay pattern
    setRequiredPolicies(PowerDevil::PolicyAgent::ChangeScreenSettings);

    auto policyAgent = PowerDevil::PolicyAgent::instance();
    connect(policyAgent, &PowerDevil::PolicyAgent::unavailablePoliciesChanged,
            this, &NightBrightness::onUnavailablePoliciesChanged);
    m_inhibitScreen = policyAgent->unavailablePolicies() & PowerDevil::PolicyAgent::ChangeScreenSettings;

    m_updateTimer.setSingleShot(true);
    connect(&m_updateTimer, &QTimer::timeout, this, &NightBrightness::applyCurrentBrightness);

    // Register keybinds: Alt+PgUp / Alt+PgDn for manual brightness override
    auto *actionCollection = new KActionCollection(this);
    actionCollection->setComponentName(u"nightbrightness"_s);
    actionCollection->setComponentDisplayName(i18nc("Name for night brightness shortcuts category", "Night Light"));

    QAction *increaseAction = actionCollection->addAction(u"Increase Night Brightness"_s);
    increaseAction->setText(i18nc("@action:inmenu Global shortcut", "Increase Night Brightness"));
    KGlobalAccel::setGlobalShortcut(increaseAction, Qt::AltModifier | Qt::Key_PageUp);
    connect(increaseAction, &QAction::triggered, this, &NightBrightness::onIncreaseBrightness);

    QAction *decreaseAction = actionCollection->addAction(u"Decrease Night Brightness"_s);
    decreaseAction->setText(i18nc("@action:inmenu Global shortcut", "Decrease Night Brightness"));
    KGlobalAccel::setGlobalShortcut(decreaseAction, Qt::AltModifier | Qt::Key_PageDown);
    connect(decreaseAction, &QAction::triggered, this, &NightBrightness::onDecreaseBrightness);
}

void NightBrightness::ensureScheduleProvider()
{
    if (m_scheduleProvider) {
        return;
    }
    // Lazily create provider — NOT in constructor (which runs during plugin discovery)
    auto stateConfig = KSharedConfig::openStateConfig();
    const QString state = stateConfig->group(u"NightBrightness"_s).readEntry(u"ScheduleState"_s);
    m_scheduleProvider = new KDarkLightScheduleProvider(state, this);

    connect(m_scheduleProvider, &KDarkLightScheduleProvider::scheduleChanged,
            this, &NightBrightness::onScheduleChanged);

    qCInfo(POWERDEVIL) << "NightBrightness: schedule provider initialized";
}

void NightBrightness::onIncreaseBrightness()
{
    if (!m_loaded) return;
    auto *ctrl = core()->screenBrightnessController();
    const auto ids = ctrl->displayIds();
    for (const QString &id : ids) {
        int current = ctrl->brightness(id);
        int maxB = ctrl->maxBrightness(id);
        int step = std::max(1, qRound(maxB * KEYBIND_STEP));
        int target = std::min(current + step, maxB);
        ctrl->setBrightness(id, target, SOURCE_NAME, u"keybind"_s, ScreenBrightnessController::ShowIndicator);
    }
    m_manualOverrideRatio = -1.0;  // signal that user overrode
    qCInfo(POWERDEVIL) << "NightBrightness: keybind increase";
}

void NightBrightness::onDecreaseBrightness()
{
    if (!m_loaded) return;
    auto *ctrl = core()->screenBrightnessController();
    const auto ids = ctrl->displayIds();
    for (const QString &id : ids) {
        int current = ctrl->brightness(id);
        int maxB = ctrl->maxBrightness(id);
        int step = std::max(1, qRound(maxB * KEYBIND_STEP));
        int target = std::max(current - step, ctrl->minBrightness(id));
        ctrl->setBrightness(id, target, SOURCE_NAME, u"keybind"_s, ScreenBrightnessController::ShowIndicator);
    }
    m_manualOverrideRatio = -1.0;  // signal that user overrode
    qCInfo(POWERDEVIL) << "NightBrightness: keybind decrease";
}

void NightBrightness::onDisplayAdded(const QString &displayId)
{
    Q_UNUSED(displayId);
    qCDebug(POWERDEVIL) << "NightBrightness: display added, reapplying";
    applyCurrentBrightness();
}

bool NightBrightness::isSupported()
{
    return core()->screenBrightnessController()->isSupported();
}

bool NightBrightness::loadAction(const PowerDevil::ProfileSettings &profileSettings)
{
    Q_UNUSED(profileSettings);  // Config is in kwinrc NightColor group, not PowerDevil profiles

    // Read config from kwinrc (shared with Night Light KCM)
    auto kwinConfig = KSharedConfig::openConfig(u"kwinrc"_s);
    auto group = kwinConfig->group(u"NightColor"_s);
    kwinConfig->reparseConfiguration();  // Ensure fresh read

    const bool enabled = group.readEntry("NightBrightnessEnabled", false);
    if (!enabled) {
        if (m_loaded) {
            qCInfo(POWERDEVIL) << "NightBrightness: disabled";
            m_updateTimer.stop();
            m_loaded = false;
            // Don't call setBrightness to restore — user adjusts slider themselves
        }
        return false;
    }

    // Lazy-init the schedule provider on first real load
    ensureScheduleProvider();

    m_bedtimePct = group.readEntry("NightBrightnessBedtimePct", 40);
    m_lateNightPct = group.readEntry("NightBrightnessLateNightPct", 20);
    m_bedtimeOffsetMin = group.readEntry("NightBrightnessBedtimeOffsetMin", 120);
    m_lateNightOffsetMin = group.readEntry("NightBrightnessLateNightOffsetMin", 180);
    m_transitionMin = group.readEntry("NightBrightnessTransitionMin", 30);

    // Ensure lateNight offset > bedtime offset
    if (m_lateNightOffsetMin <= m_bedtimeOffsetMin) {
        m_lateNightOffsetMin = m_bedtimeOffsetMin + m_transitionMin;
    }

    qCInfo(POWERDEVIL) << "NightBrightness: loaded — bedtime" << m_bedtimePct << "% at +"
                       << m_bedtimeOffsetMin << "min, lateNight" << m_lateNightPct << "% at +"
                       << m_lateNightOffsetMin << "min, transition" << m_transitionMin << "min";

    // Connect to display hotplug for reapply
    connect(core()->screenBrightnessController(), &ScreenBrightnessController::displayAdded,
            this, &NightBrightness::onDisplayAdded, Qt::UniqueConnection);

    // Clear any manual override when config reloads
    m_manualOverrideRatio.reset();

    m_loaded = true;
    applyCurrentBrightness();
    return true;
}

void NightBrightness::onProfileUnload()
{
    if (m_loaded) {
        qCInfo(POWERDEVIL) << "NightBrightness: profile unloaded";
        m_updateTimer.stop();
        m_loaded = false;
        // Don't call setBrightness to restore — avoids DDC/CI contention
    }
}

void NightBrightness::onScheduleChanged()
{
    qCDebug(POWERDEVIL) << "NightBrightness: schedule changed";

    if (m_scheduleProvider) {
        // Persist for faster startup next time
        auto stateConfig = KSharedConfig::openStateConfig();
        stateConfig->group(u"NightBrightness"_s).writeEntry(u"ScheduleState"_s, m_scheduleProvider->state());
        stateConfig->sync();
    }

    if (m_loaded) {
        applyCurrentBrightness();
    }
}

void NightBrightness::onUnavailablePoliciesChanged(PowerDevil::PolicyAgent::RequiredPolicies policies)
{
    const auto wasInhibited = m_inhibitScreen;
    m_inhibitScreen = policies & PowerDevil::PolicyAgent::ChangeScreenSettings;

    if (m_inhibitScreen && !wasInhibited) {
        qCInfo(POWERDEVIL) << "NightBrightness: inhibited, pausing";
        m_updateTimer.stop();
        // Don't restore brightness — user controls it during inhibition
    } else if (!m_inhibitScreen && wasInhibited && m_loaded) {
        qCDebug(POWERDEVIL) << "NightBrightness: inhibition cleared, reapplying";
        applyCurrentBrightness();
    }
}

double NightBrightness::computeRatioForTime(const QDateTime &time) const
{
    if (!m_scheduleProvider) {
        return 1.0;
    }
    const auto schedule = m_scheduleProvider->schedule();
    const auto prevTransition = schedule.previousTransition(time);
    const auto nextTransition = schedule.nextTransition(time);

    if (!prevTransition || !nextTransition) {
        // Extreme latitude — no sunrise/sunset. Stay at full brightness.
        return 1.0;
    }

    const double bedtimeRatio = m_bedtimePct / 100.0;
    const double lateNightRatio = m_lateNightPct / 100.0;

    // Check if we're in an evening transition (sunset in progress)
    if (nextTransition->type() == KDarkLightTransition::Evening
        && nextTransition->test(time) == KDarkLightTransition::InProgress) {
        // Sunset is happening now — start dimming
        const double progress = nextTransition->progress(time);
        return std::lerp(1.0, bedtimeRatio, progress);
    }

    // Check if we're in a morning transition (sunrise in progress)
    if (nextTransition->type() == KDarkLightTransition::Morning
        && nextTransition->test(time) == KDarkLightTransition::InProgress) {
        // Sunrise is happening now — brightening
        const double progress = nextTransition->progress(time);
        return std::lerp(lateNightRatio, 1.0, progress);
    }

    // If previous transition was morning → we're in daytime
    if (prevTransition->type() == KDarkLightTransition::Morning) {
        return 1.0;
    }

    // Previous transition was evening → we're in nighttime
    // Calculate position within the nighttime tier system
    const QDateTime sunsetEnd = prevTransition->endDateTime();
    const qint64 secsSinceSunset = sunsetEnd.secsTo(time);
    const qint64 bedtimeStartSecs = m_bedtimeOffsetMin * 60;
    const qint64 lateNightStartSecs = m_lateNightOffsetMin * 60;
    const qint64 transitionSecs = m_transitionMin * 60;

    if (secsSinceSunset < 0) {
        // Should not happen, but safety
        return 1.0;
    }

    // Phase 1: Post-sunset hold at bedtime level (evening transition already handled above)
    if (secsSinceSunset < bedtimeStartSecs) {
        return bedtimeRatio;
    }

    // Phase 2: Bedtime → late-night transition
    if (secsSinceSunset < bedtimeStartSecs + transitionSecs) {
        const double progress = double(secsSinceSunset - bedtimeStartSecs) / double(transitionSecs);
        return std::lerp(bedtimeRatio, lateNightRatio, std::clamp(progress, 0.0, 1.0));
    }

    // Phase 3: Late-night hold
    return lateNightRatio;
}

void NightBrightness::applyCurrentBrightness()
{
    if (!m_loaded || m_inhibitScreen) {
        return;
    }

    // If user manually overrode (keybind or slider), skip until next tier boundary
    if (m_manualOverrideRatio.has_value()) {
        // Clear override if we haven't already and schedule changed enough
        m_manualOverrideRatio.reset();
    }

    const double ratio = computeRatioForTime(QDateTime::currentDateTime());

    // Only touch brightness when dimming — leave it alone during daytime (ratio ~1.0)
    // This prevents boot-time DDC/CI failures from killing brightness controls
    if (ratio >= 0.99) {
        qCInfo(POWERDEVIL) << "NightBrightness: daytime, not touching brightness";
        scheduleNextUpdate();
        return;
    }

    // Set absolute DDC/CI brightness on all displays
    auto *ctrl = core()->screenBrightnessController();
    const auto ids = ctrl->displayIds();
    for (const QString &id : ids) {
        int maxB = ctrl->maxBrightness(id);
        int target = qRound(maxB * ratio);
        target = std::clamp(target, ctrl->minBrightness(id), maxB);
        qCInfo(POWERDEVIL) << "NightBrightness: setting" << id << "to" << target << "/" << maxB
                           << "(" << qRound(ratio * 100) << "%)";
        ctrl->setBrightness(id, target, SOURCE_NAME, u"scheduled"_s,
                           ScreenBrightnessController::SuppressIndicator);
    }

    scheduleNextUpdate();
}

void NightBrightness::scheduleNextUpdate()
{
    const QDateTime now = QDateTime::currentDateTime();
    const double currentRatio = computeRatioForTime(now);

    // Find the next time the ratio changes by at least 2%
    // Check at 60-second increments, up to 24 hours ahead
    constexpr int CHECK_INTERVAL_SECS = 60;
    constexpr int MAX_LOOKAHEAD_SECS = 24 * 3600;
    constexpr double MIN_CHANGE = 0.02;

    int bestDelaySecs = MAX_LOOKAHEAD_SECS;

    for (int secs = CHECK_INTERVAL_SECS; secs <= MAX_LOOKAHEAD_SECS; secs += CHECK_INTERVAL_SECS) {
        const QDateTime futureTime = now.addSecs(secs);
        const double futureRatio = computeRatioForTime(futureTime);

        if (std::abs(futureRatio - currentRatio) >= MIN_CHANGE) {
            bestDelaySecs = secs;
            break;
        }
    }

    // Enforce minimum spacing of 180 seconds between updates
    bestDelaySecs = std::max(bestDelaySecs, 180);

    qCDebug(POWERDEVIL) << "NightBrightness: next update in" << bestDelaySecs << "seconds";
    m_updateTimer.start(std::chrono::seconds(bestDelaySecs));
}

} // namespace PowerDevil::BundledActions

#include "nightbrightness.moc"

#include "moc_nightbrightness.cpp"
'''

PLUGIN_JSON = '''{
    "KPlugin": {
        "Description": "Adjusts display brightness based on time of day",
        "Icon": "brightness-auto",
        "Name": "Night Brightness"
    },
    "X-KDE-PowerDevil-Action-HasRuntimeRequirement": true,
    "X-KDE-PowerDevil-Action-ID": "NightBrightness"
}
'''


# KCFG_ENTRIES and KCM_QML removed — config and UI now in plasma-workspace Night Light KCM
# Use patch-plasma-nightbrightness.py for the UI side



if __name__ == '__main__':
    main()
