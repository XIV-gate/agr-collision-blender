"""Verify that every visible AGR Collision UI string has a Russian entry."""

import ast
import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "xivgate_agr_collision"


class _FakeTranslations:
    def register(self, _module_name, _translations):
        pass

    def unregister(self, _module_name):
        pass

    @staticmethod
    def pgettext_iface(message):
        return message


sys.modules.setdefault(
    "bpy",
    types.SimpleNamespace(
        app=types.SimpleNamespace(translations=_FakeTranslations()),
    ),
)

spec = importlib.util.spec_from_file_location(
    "agr_collision_translations",
    PACKAGE / "translations.py",
)
translations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(translations)


def _literal_string(node):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, str) and value else None


def _call_name(call):
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _keyword_string(call, keyword_name):
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return _literal_string(keyword.value)
    return None


def _visible_strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    default = set()
    operators = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = _call_name(node)
            if call_name.endswith("Property"):
                for keyword_name in ("name", "description"):
                    value = _keyword_string(node, keyword_name)
                    if value:
                        default.add(value)
            elif call_name == "label":
                value = _keyword_string(node, "text")
                if value:
                    default.add(value)
            elif call_name == "operator":
                value = _keyword_string(node, "text")
                if value:
                    operators.add(value)
            elif call_name == "iface" and node.args:
                value = _literal_string(node.args[0])
                if value:
                    default.add(value)

        if not isinstance(node, ast.ClassDef):
            continue
        is_operator = any(
            isinstance(base, ast.Attribute) and base.attr == "Operator"
            for base in node.bases
        )
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            targets = [
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name)
            ]
            value = _literal_string(statement.value)
            if not value:
                continue
            if "bl_label" in targets:
                (operators if is_operator else default).add(value)
            if "bl_description" in targets:
                default.add(value)

    return default, operators


required_default = set()
required_operators = set()
for filename in ("props.py", "panel.py", "operators.py"):
    default, operators = _visible_strings(PACKAGE / filename)
    required_default.update(default)
    required_operators.update(operators)

ru = translations.TRANSLATIONS["ru_RU"]
default_keys = {
    message
    for (context, message), _translated in ru.items()
    if context == "*"
}
operator_keys = {
    message
    for (context, message), _translated in ru.items()
    if context == "Operator"
}

missing_default = sorted(required_default - default_keys)
missing_operators = sorted(
    message
    for message in required_operators
    if message not in operator_keys and message not in default_keys
)

assert not missing_default, "Missing default-context translations: {}".format(
    missing_default
)
assert not missing_operators, "Missing operator translations: {}".format(
    missing_operators
)

print(
    "AGR_TRANSLATION_COVERAGE_RESULT",
    {
        "default": len(required_default),
        "operators": len(required_operators),
    },
)
