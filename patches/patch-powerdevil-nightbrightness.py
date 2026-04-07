#!/usr/bin/env python3
"""
Patch PowerDevil to add NightBrightness action.
Adds time-based DDC/CI brightness scheduling using setBrightness() + KNightTime.

v2: Bug fixes — daytime brightness config, deferred first-load, manual override fix,
    suspend/resume handler, dead code removal.

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
    print("[1/5] Creating nightbrightness.h")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'nightbrightness.h'), NIGHTBRIGHTNESS_H)

    # 2. Create nightbrightness.cpp
    print("[2/5] Creating nightbrightness.cpp")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'nightbrightness.cpp'), NIGHTBRIGHTNESS_CPP)

    # 3. Create plugin JSON
    print("[3/5] Creating plugin JSON")
    write_file(os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'powerdevilnightbrightnessaction.json'), PLUGIN_JSON)

    # 4. Patch daemon/actions/bundled/CMakeLists.txt
    print("[4/5] Patching actions CMakeLists.txt")
    ok = patch_file(
        os.path.join(srcdir, 'daemon', 'actions', 'bundled', 'CMakeLists.txt'),
        'add_powerdevil_bundled_action(dimdisplay)',
        'add_powerdevil_bundled_action(dimdisplay)\n\nadd_powerdevil_bundled_action(nightbrightness)\ntarget_link_libraries(powerdevil_nightbrightnessaction KNightTime KF6::GlobalAccel KF6::XmlGui)'
    )
    if ok:
        print("  OK")

    # 5. Patch top-level CMakeLists.txt — add find_package(KNightTime)
    print("[5/5] Patching top-level CMakeLists.txt")
    ok = patch_file(
        os.path.join(srcdir, 'CMakeLists.txt'),
        'find_package(DDCUtil)',
        'find_package(KNightTime REQUIRED)\nfind_package(DDCUtil)'
    )
    if ok:
        print("  OK")

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

public Q_SLOTS:
    void onPrepareForSleep(bool sleeping);

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
    void applyBrightnessNow(int retriesRemaining = 1);
    void scheduleNextUpdate();
    double computeRatioForTime(const QDateTime &time) const;

    // Configuration
    int m_daytimePct = 100;
    int m_bedtimePct = 40;
    int m_lateNightPct = 20;
    int m_bedtimeOffsetMin = 120;
    int m_lateNightOffsetMin = 180;
    int m_transitionMin = 30;
    bool m_loaded = false;

    // Schedule — lazily initialized in loadAction, NOT constructor
    KDarkLightScheduleProvider *m_scheduleProvider = nullptr;
    QTimer m_updateTimer;

    // Boot/resume deferred write
    bool m_resumePending = false;

    // Manual override (keybinds)
    bool m_manualOverrideActive = false;
    double m_lastScheduledRatio = 1.0;

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
#include <QDBusConnection>
#include <QDateTime>
#include <QPointer>
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

    // Connect to Login1 PrepareForSleep for suspend/resume
    QDBusConnection::systemBus().connect(
        u"org.freedesktop.login1"_s,
        u"/org/freedesktop/login1"_s,
        u"org.freedesktop.login1.Manager"_s,
        u"PrepareForSleep"_s,
        this, SLOT(onPrepareForSleep(bool)));

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
    m_manualOverrideActive = true;
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
    m_manualOverrideActive = true;
    qCInfo(POWERDEVIL) << "NightBrightness: keybind decrease";
}

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
        qCInfo(POWERDEVIL) << "NightBrightness: hotplug set" << displayId << "to" << target
                           << "/" << maxB << "(" << qRound(ratio * 100) << "%)";
    }
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

    m_daytimePct = group.readEntry("NightBrightnessDaytimePct", 100);
    m_bedtimePct = group.readEntry("NightBrightnessBedtimePct", 40);
    m_lateNightPct = group.readEntry("NightBrightnessLateNightPct", 20);
    m_bedtimeOffsetMin = group.readEntry("NightBrightnessBedtimeOffsetMin", 120);
    m_lateNightOffsetMin = group.readEntry("NightBrightnessLateNightOffsetMin", 180);
    m_transitionMin = group.readEntry("NightBrightnessTransitionMin", 30);

    // Ensure lateNight offset > bedtime offset
    if (m_lateNightOffsetMin <= m_bedtimeOffsetMin) {
        m_lateNightOffsetMin = m_bedtimeOffsetMin + m_transitionMin;
    }

    qCInfo(POWERDEVIL) << "NightBrightness: loaded — daytime" << m_daytimePct << "%, bedtime"
                       << m_bedtimePct << "% at +" << m_bedtimeOffsetMin << "min, lateNight"
                       << m_lateNightPct << "% at +" << m_lateNightOffsetMin << "min, transition"
                       << m_transitionMin << "min";

    // Connect to display hotplug for reapply
    connect(core()->screenBrightnessController(), &ScreenBrightnessController::displayAdded,
            this, &NightBrightness::onDisplayAdded, Qt::UniqueConnection);

    // Clear any manual override when config reloads
    m_manualOverrideActive = false;

    // Boot and config-reload writes are immediate (detection complete before loadAction)
    // Only onPrepareForSleep sets m_resumePending for resume path

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

void NightBrightness::onPrepareForSleep(bool sleeping)
{
    if (!sleeping && m_loaded) {
        // Waking up — DDC/CI may need re-initialization, defer write
        m_manualOverrideActive = false;  // pre-sleep override is stale
        m_resumePending = true;
        applyCurrentBrightness();
    }
}

double NightBrightness::computeRatioForTime(const QDateTime &time) const
{
    const double daytimeRatio = m_daytimePct / 100.0;

    if (!m_scheduleProvider) {
        return daytimeRatio;
    }
    const auto schedule = m_scheduleProvider->schedule();
    const auto prevTransition = schedule.previousTransition(time);
    const auto nextTransition = schedule.nextTransition(time);

    if (!prevTransition || !nextTransition) {
        // Extreme latitude — no sunrise/sunset. Return configured daytime brightness.
        return daytimeRatio;
    }

    const double bedtimeRatio = m_bedtimePct / 100.0;
    const double lateNightRatio = m_lateNightPct / 100.0;

    // Check if we're in an evening transition (sunset in progress)
    if (nextTransition->type() == KDarkLightTransition::Evening
        && nextTransition->test(time) == KDarkLightTransition::InProgress) {
        // Sunset is happening now — start dimming from daytime to bedtime
        const double progress = nextTransition->progress(time);
        return std::lerp(daytimeRatio, bedtimeRatio, progress);
    }

    // Check if we're in a morning transition (sunrise in progress)
    if (nextTransition->type() == KDarkLightTransition::Morning
        && nextTransition->test(time) == KDarkLightTransition::InProgress) {
        // Sunrise is happening now — compute actual pre-sunrise nighttime ratio
        const double progress = nextTransition->progress(time);

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
    }

    // If previous transition was morning -> we're in daytime
    if (prevTransition->type() == KDarkLightTransition::Morning) {
        return daytimeRatio;
    }

    // Previous transition was evening -> we're in nighttime
    // Calculate position within the nighttime tier system
    const QDateTime sunsetEnd = prevTransition->endDateTime();
    const qint64 secsSinceSunset = sunsetEnd.secsTo(time);
    const qint64 bedtimeStartSecs = m_bedtimeOffsetMin * 60;
    const qint64 lateNightStartSecs = m_lateNightOffsetMin * 60;
    const qint64 transitionSecs = m_transitionMin * 60;

    if (secsSinceSunset < 0) {
        // Should not happen, but safety
        return daytimeRatio;
    }

    // Phase 1: Post-sunset hold at bedtime level
    if (secsSinceSunset < bedtimeStartSecs) {
        return bedtimeRatio;
    }

    // Phase 1.5: Extended bedtime hold (gap between bedtime and late-night transition)
    const qint64 lateTransitionStart = lateNightStartSecs - transitionSecs;
    if (secsSinceSunset < lateTransitionStart) {
        return bedtimeRatio;
    }

    // Phase 2: Bedtime -> late-night transition (ends at lateNightStartSecs)
    if (secsSinceSunset < lateNightStartSecs) {
        const double progress = double(secsSinceSunset - lateTransitionStart) / double(transitionSecs);
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

    // Manual override logic: if user adjusted via keybind, skip until tier boundary
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

    // Steady-state: apply immediately
    applyBrightnessNow();
    scheduleNextUpdate();
}

void NightBrightness::applyBrightnessNow(int retriesRemaining)
{
    if (m_manualOverrideActive) return;  // user took control during delay
    if (!m_loaded || m_inhibitScreen) return;

    const double ratio = computeRatioForTime(QDateTime::currentDateTime());
    m_lastScheduledRatio = ratio;  // keep override baseline current

    auto *ctrl = core()->screenBrightnessController();
    if (!ctrl) return;
    const auto ids = ctrl->displayIds();

    if (ids.isEmpty()) {
        if (retriesRemaining > 0) {
            QTimer::singleShot(std::chrono::milliseconds(1500), this,
                [this, guard = QPointer(this), retriesRemaining]() {
                    if (!guard || !m_loaded) return;
                    applyBrightnessNow(retriesRemaining - 1);
                });
            qCInfo(POWERDEVIL) << "NightBrightness: no displays yet, retrying in 5s";
        } else {
            qCWarning(POWERDEVIL) << "NightBrightness: no displays after retries, giving up";
        }
        return;
    }

    for (const QString &id : ids) {
        int maxB = ctrl->maxBrightness(id);
        int target = qRound(maxB * ratio);
        target = std::clamp(target, ctrl->minBrightness(id), maxB);
        int current = ctrl->brightness(id);
        if (current == target) continue;  // skip redundant DDC/CI write
        ctrl->setBrightness(id, target, SOURCE_NAME, u"scheduled"_s,
                           ScreenBrightnessController::SuppressIndicator);
        qCInfo(POWERDEVIL) << "NightBrightness: set" << id << "to" << target
                           << "/" << maxB << "(" << qRound(ratio * 100) << "%)";
    }
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


if __name__ == '__main__':
    main()
