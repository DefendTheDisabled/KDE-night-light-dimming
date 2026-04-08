#!/usr/bin/env python3
"""Replace Configure button with inline KeySequenceItems in Night Light KCM."""

filepath = '/home/sean/nightbrightness/plasma-workspace-pkg/src/plasma-workspace-6.6.3/kcms/nightlight/ui/main.qml'
with open(filepath, 'r') as f:
    content = f.read()

old = (
    '            RowLayout {\n'
    '                Kirigami.FormData.label: i18nc("@label", "Shortcuts:")\n'
    '                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled\n'
    '                spacing: Kirigami.Units.smallSpacing\n'
    '\n'
    '                QQC2.Label {\n'
    '                    text: i18nc("@info", "Alt+PgUp / Alt+PgDn")\n'
    '                    textFormat: Text.PlainText\n'
    '                }\n'
    '\n'
    '                QQC2.Button {\n'
    '                    icon.name: "configure-shortcuts"\n'
    '                    text: i18nc("@action:button", "Configure\u2026")\n'
    '                    onClicked: KCM.KCMLauncher.openSystemSettings("kcm_keys", ["kwin"])\n'
    '                }\n'
    '            }'
)

new = (
    '            RowLayout {\n'
    '                Kirigami.FormData.label: i18nc("@label", "Increase brightness:")\n'
    '                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled\n'
    '                spacing: Kirigami.Units.smallSpacing\n'
    '\n'
    '                KQuickControls.KeySequenceItem {\n'
    '                    keySequence: kcm.shortcutForAction("Night Brightness Up")\n'
    '                    onKeySequenceModified: kcm.setShortcutForAction("Night Brightness Up", keySequence)\n'
    '                }\n'
    '            }\n'
    '\n'
    '            RowLayout {\n'
    '                Kirigami.FormData.label: i18nc("@label", "Decrease brightness:")\n'
    '                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled\n'
    '                spacing: Kirigami.Units.smallSpacing\n'
    '\n'
    '                KQuickControls.KeySequenceItem {\n'
    '                    keySequence: kcm.shortcutForAction("Night Brightness Down")\n'
    '                    onKeySequenceModified: kcm.setShortcutForAction("Night Brightness Down", keySequence)\n'
    '                }\n'
    '            }'
)

if old not in content:
    print('ERROR: Pattern not found')
else:
    content = content.replace(old, new, 1)
    with open(filepath, 'w') as f:
        f.write(content)
    print('Replaced with KeySequenceItems')
