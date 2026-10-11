"""Fixed EF signal outlet on the immutable standalone E native code."""
from __future__ import annotations

import ast
import re


OLD_COMPONENT = "component = (signals[2] if self.MODE == 'E' else -signals[1])"
PAIR_COMPONENT = "component = (signals[2] + signals[1]) / 2.0"


def render_efficiency_f141_pair(e_code, class_name="WC03EfficiencyF141Pair"):
    """Change only class identity and the final equal-signal component.

    SOURCE/E_SOURCE, original G/F/E support and variance gates, peer-price F,
    ranks, winsorization, population Z and all calendars remain identical.
    The inherited MODE literal is intentionally retained; the new component
    is unconditional, so this independent class has exactly one EF definition.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", class_name):
        raise ValueError("Invalid independent EF class")
    tree = ast.parse(e_code)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if len(classes) != 1:
        raise ValueError("Expected one bound standalone E class")
    cls = classes[0]
    literal = {t.id: ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
    if literal.get("MODE") != "E" or not {"SOURCE", "E_SOURCE", "INPUT_CALENDAR"}.issubset(literal):
        raise ValueError("Expected the complete bound standalone E native template")
    old_class = "class " + cls.name + "(Factor):\n"
    if e_code.count(old_class) != 1 or e_code.count(OLD_COMPONENT) != 1 or class_name == cls.name:
        raise ValueError("Bound E class/component template drift")
    code = e_code.replace(old_class, "class " + class_name + "(Factor):\n", 1)
    code = code.replace(OLD_COMPONENT, PAIR_COMPONENT, 1)

    # No other operator, source, gate, parameter or final normalization changes.
    cls.name = class_name
    assignments = [n for n in ast.walk(cls) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "component" for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError("Ambiguous bound E component")
    assignments[0].value = ast.parse("(signals[2] + signals[1]) / 2.0", mode="eval").body
    if ast.dump(tree, include_attributes=False) != ast.dump(ast.parse(code), include_attributes=False):
        raise ValueError("EF wrapper changed more than its class and equal-signal outlet")
    return code
