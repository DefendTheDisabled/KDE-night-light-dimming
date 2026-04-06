#!/usr/bin/env python3
"""
Patch plasma-workspace Night Light KCM to add Night Brightness controls.
Adds time-based brightness scheduling UI alongside color temperature controls.

Usage: python3 patch-plasma-nightbrightness.py /path/to/plasma-workspace-6.6.3/
"""

import sys
import os

def patch_file(filepath, old, new):
    """Replace old text with new text in file."""
    with open(filepath, 'r') as f:
        content = f.read()
    if old not in content:
        print(f"  WARNING: Pattern not found in {filepath}")
        print(f"  Looking for: {repr(old[:80])}...")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    return True

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/plasma-workspace-source/")
        sys.exit(1)

    srcdir = sys.argv[1]
    kcfg = os.path.join(srcdir, 'kcms', 'nightlight', 'nightlightsettings.kcfg')
    qml = os.path.join(srcdir, 'kcms', 'nightlight', 'ui', 'main.qml')

    if not os.path.exists(kcfg):
        print(f"Error: {kcfg} not found")
        sys.exit(1)

    print("=== Patching plasma-workspace Night Light KCM ===\n")

    # 1. Add brightness entries to nightlightsettings.kcfg
    print("[1/2] Patching nightlightsettings.kcfg")
    ok = patch_file(kcfg,
        '        <entry name="NightTemperature" type="Int">',
        KCFG_ENTRIES + '        <entry name="NightTemperature" type="Int">')
    if ok:
        print("  OK")

    # 2. Add brightness controls to main.qml
    print("[2/2] Patching main.qml")
    # Insert after the night temperature GridLayout, before FormLayout closes
    # Anchor: the unique end of the night temperature section
    ok = patch_file(qml,
        '                QQC2.Label {\n'
        '                    text: i18nc("Night colour red-ish", "Warm")\n'
        '                    textFormat: Text.PlainText\n'
        '                }\n'
        '                Item {}\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '}',

        '                QQC2.Label {\n'
        '                    text: i18nc("Night colour red-ish", "Warm")\n'
        '                    textFormat: Text.PlainText\n'
        '                }\n'
        '                Item {}\n'
        '            }\n'
        + QML_CONTROLS +
        '        }\n'
        '    }\n'
        '}')
    if ok:
        print("  OK")

    print("\n=== Patching complete ===")


# ============================================================
# Content
# ============================================================

KCFG_ENTRIES = '''        <!-- Night Brightness settings -->
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
'''

QML_CONTROLS = '''
            // === Night Brightness ===
            QQC2.CheckBox {
                id: nightBrightnessCheck
                Kirigami.FormData.label: i18nc("@label:checkbox", "Night brightness:")
                text: i18nc("@option:check", "Dim screen brightness at night")
                enabled: kcm.nightLightSettings.active
                checked: kcm.nightLightSettings.nightBrightnessEnabled
                onToggled: kcm.nightLightSettings.nightBrightnessEnabled = checked

                KCM.SettingStateBinding {
                    configObject: kcm.nightLightSettings
                    settingName: "NightBrightnessEnabled"
                    extraEnabledConditions: kcm.nightLightSettings.active
                }
            }

            GridLayout {
                Kirigami.FormData.label: i18nc("@label:slider", "Bedtime brightness:")
                Kirigami.FormData.buddyFor: bedtimeSlider
                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                columns: 4

                QQC2.Slider {
                    id: bedtimeSlider
                    Layout.minimumWidth: modeSwitcher.width
                    Layout.columnSpan: 3
                    from: 100
                    to: 5
                    stepSize: -5
                    live: true
                    value: kcm.nightLightSettings.nightBrightnessBedtimePct
                    onMoved: kcm.nightLightSettings.nightBrightnessBedtimePct = value

                    KCM.SettingStateBinding {
                        configObject: kcm.nightLightSettings
                        settingName: "NightBrightnessBedtimePct"
                        extraEnabledConditions: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                    }
                }
                QQC2.Label {
                    text: i18nc("Brightness percentage", "%1%", bedtimeSlider.value)
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignRight
                    Layout.minimumWidth: sliderValueLabelMetrics.implicitWidth
                }
                //row 2
                QQC2.Label {
                    text: i18nc("Bright, no dimming", "Bright (no dimming)")
                    textFormat: Text.PlainText
                }
                Item { Layout.fillWidth: true }
                QQC2.Label {
                    text: i18nc("Dim brightness", "Dim")
                    textFormat: Text.PlainText
                }
                Item {}
            }

            QQC2.SpinBox {
                Kirigami.FormData.label: i18nc("@label:spinbox", "Bedtime starts:")
                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                from: 0; to: 720; stepSize: 15
                editable: false
                value: kcm.nightLightSettings.nightBrightnessBedtimeOffsetMin
                onValueModified: kcm.nightLightSettings.nightBrightnessBedtimeOffsetMin = value
                textFromValue: function(value) {
                    var h = Math.floor(value / 60);
                    var m = value % 60;
                    return h > 0 ? i18nc("hours and minutes after sunset", "%1h %2min after sunset", h, m)
                                 : i18nc("minutes after sunset", "%1 min after sunset", m);
                }

                KCM.SettingStateBinding {
                    configObject: kcm.nightLightSettings
                    settingName: "NightBrightnessBedtimeOffsetMin"
                    extraEnabledConditions: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                }
            }

            GridLayout {
                Kirigami.FormData.label: i18nc("@label:slider", "Late night brightness:")
                Kirigami.FormData.buddyFor: lateNightSlider
                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                columns: 4

                QQC2.Slider {
                    id: lateNightSlider
                    Layout.minimumWidth: modeSwitcher.width
                    Layout.columnSpan: 3
                    from: 100
                    to: 5
                    stepSize: -5
                    live: true
                    value: kcm.nightLightSettings.nightBrightnessLateNightPct
                    onMoved: kcm.nightLightSettings.nightBrightnessLateNightPct = value

                    KCM.SettingStateBinding {
                        configObject: kcm.nightLightSettings
                        settingName: "NightBrightnessLateNightPct"
                        extraEnabledConditions: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                    }
                }
                QQC2.Label {
                    text: i18nc("Brightness percentage", "%1%", lateNightSlider.value)
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignRight
                    Layout.minimumWidth: sliderValueLabelMetrics.implicitWidth
                }
                //row 2
                QQC2.Label {
                    text: i18nc("Bright, no dimming", "Bright (no dimming)")
                    textFormat: Text.PlainText
                }
                Item { Layout.fillWidth: true }
                QQC2.Label {
                    text: i18nc("Dim brightness", "Dim")
                    textFormat: Text.PlainText
                }
                Item {}
            }

            QQC2.SpinBox {
                Kirigami.FormData.label: i18nc("@label:spinbox", "Late night starts:")
                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                from: 0; to: 720; stepSize: 15
                editable: false
                value: kcm.nightLightSettings.nightBrightnessLateNightOffsetMin
                onValueModified: kcm.nightLightSettings.nightBrightnessLateNightOffsetMin = value
                textFromValue: function(value) {
                    var h = Math.floor(value / 60);
                    var m = value % 60;
                    return h > 0 ? i18nc("hours and minutes after sunset", "%1h %2min after sunset", h, m)
                                 : i18nc("minutes after sunset", "%1 min after sunset", m);
                }

                KCM.SettingStateBinding {
                    configObject: kcm.nightLightSettings
                    settingName: "NightBrightnessLateNightOffsetMin"
                    extraEnabledConditions: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                }
            }

            QQC2.SpinBox {
                Kirigami.FormData.label: i18nc("@label:spinbox", "Transition duration:")
                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                from: 1; to: 120; stepSize: 5
                editable: false
                value: kcm.nightLightSettings.nightBrightnessTransitionMin
                onValueModified: kcm.nightLightSettings.nightBrightnessTransitionMin = value
                textFromValue: function(value) { return i18nc("minutes", "%1 min", value); }

                KCM.SettingStateBinding {
                    configObject: kcm.nightLightSettings
                    settingName: "NightBrightnessTransitionMin"
                    extraEnabledConditions: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled
                }
            }

'''


if __name__ == '__main__':
    main()
