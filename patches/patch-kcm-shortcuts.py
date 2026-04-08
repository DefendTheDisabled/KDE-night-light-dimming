#!/usr/bin/env python3
"""Patch Night Light KCM to add inline shortcut editing."""
import sys

def patch(filepath, old, new):
    with open(filepath, "r") as f:
        content = f.read()
    if old not in content:
        print(f"  WARN: not found in {filepath}: {old[:60]}...")
        return False
    content = content.replace(old, new, 1)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  Patched {filepath}")
    return True

srcdir = sys.argv[1]

# 1. CMakeLists.txt — add KF6::GlobalAccel
print("[1/4] CMakeLists.txt")
patch(f"{srcdir}/kcms/nightlight/CMakeLists.txt",
    "    KNightTime\n)",
    "    KNightTime\n    KF6::GlobalAccel\n)")

# 2. kcm.h — add include and Q_INVOKABLE methods
print("[2/4] kcm.h")
patch(f"{srcdir}/kcms/nightlight/kcm.h",
    "#include <KQuickManagedConfigModule>",
    "#include <KQuickManagedConfigModule>\n#include <QKeySequence>")

patch(f"{srcdir}/kcms/nightlight/kcm.h",
    "    Q_INVOKABLE void preview(uint temperature);\n    Q_INVOKABLE void stopPreview();",
    "    Q_INVOKABLE void preview(uint temperature);\n"
    "    Q_INVOKABLE void stopPreview();\n"
    "\n"
    "    Q_INVOKABLE QVariant shortcutForAction(const QString &actionName) const;\n"
    "    Q_INVOKABLE void setShortcutForAction(const QString &actionName, const QVariant &keySequence);")

# 3. kcm.cpp — add include and implementations
print("[3/4] kcm.cpp")
patch(f"{srcdir}/kcms/nightlight/kcm.cpp",
    "#include <KLocalizedString>\n#include <KPluginFactory>",
    "#include <KGlobalAccel>\n#include <KLocalizedString>\n#include <KPluginFactory>")

SHORTCUT_METHODS = (
    'QVariant KCMNightLight::shortcutForAction(const QString &actionName) const\n'
    '{\n'
    '    QAction act;\n'
    '    act.setObjectName(actionName);\n'
    '    act.setProperty("componentName", QStringLiteral("kwin"));\n'
    '    const auto shortcuts = KGlobalAccel::self()->shortcut(&act);\n'
    '    if (shortcuts.isEmpty()) {\n'
    '        return QVariant::fromValue(QKeySequence());\n'
    '    }\n'
    '    return QVariant::fromValue(shortcuts.first());\n'
    '}\n'
    '\n'
    'void KCMNightLight::setShortcutForAction(const QString &actionName, const QVariant &keySequence)\n'
    '{\n'
    '    QAction act;\n'
    '    act.setObjectName(actionName);\n'
    '    act.setProperty("componentName", QStringLiteral("kwin"));\n'
    '    KGlobalAccel::self()->setShortcut(&act, {keySequence.value<QKeySequence>()}, KGlobalAccel::NoAutoloading);\n'
    '}\n'
    '\n'
)

patch(f"{srcdir}/kcms/nightlight/kcm.cpp",
    "void KCMNightLight::save()",
    SHORTCUT_METHODS + "void KCMNightLight::save()")

# 4. main.qml — replace shortcut label/button with inline KeySequenceItems
print("[4/4] main.qml")

OLD_SHORTCUT_SECTION = (
    '            RowLayout {\n'
    '                Kirigami.FormData.label: i18nc("@label", "Shortcuts:")\n'
    '                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled\n'
    '                spacing: Kirigami.Units.smallSpacing\n'
    '\n'
    '                QQC2.Label {\n'
    '                    text: i18nc("@info", "Adjust brightness with Alt+PgUp / Alt+PgDn (in Shortcuts > Window Management)")\n'
    '                    textFormat: Text.PlainText\n'
    '                }\n'
    '\n'
    '                QQC2.Button {\n'
    '                    icon.name: "configure-shortcuts"\n'
    '                    text: i18nc("@action:button", "Configure\\u2026")\n'
    '                    onClicked: KCM.KCMLauncher.openSystemSettings("kcm_keys")\n'
    '                }\n'
    '            }'
)

NEW_SHORTCUT_SECTION = (
    '            RowLayout {\n'
    '                Kirigami.FormData.label: i18nc("@label", "Increase brightness:")\n'
    '                enabled: kcm.nightLightSettings.active && kcm.nightLightSettings.nightBrightnessEnabled\n'
    '                spacing: Kirigami.Units.smallSpacing\n'
    '\n'
    '                KQuickControls.KeySequenceItem {\n'
    '                    keySequence: kcm.shortcutForAction("Night Brightness Up")\n'
    '                    onKeySequenceModified: kcm.setShortcutForAction("Night Brightness Up", keySequence)\n'
    '                    checkForConflictsAgainst: ShortcutType.GlobalShortcuts\n'
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
    '                    checkForConflictsAgainst: ShortcutType.GlobalShortcuts\n'
    '                }\n'
    '            }'
)

patch(f"{srcdir}/kcms/nightlight/ui/main.qml",
    OLD_SHORTCUT_SECTION,
    NEW_SHORTCUT_SECTION)

# Add import for KQuickControls
patch(f"{srcdir}/kcms/nightlight/ui/main.qml",
    "import org.kde.private.kcms.nightlight as Private",
    "import org.kde.kquickcontrols as KQuickControls\nimport org.kde.private.kcms.nightlight as Private")

print("Done")
