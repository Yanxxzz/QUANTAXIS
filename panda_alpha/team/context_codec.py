"""Lossless prompt-only aliases; canonical work packets stay unchanged."""
from __future__ import annotations

from .contracts import canonical

MARKER = "$panda_same_as"
FORMAT = "panda-context-refs-v1"
PRIORITY = ("packet", "source_material", "independent_source_analyses", "task", "qualifications", "review_targets")


def encode(value, *, minimum_bytes=4096):
    """Alias exact repeated values, never summarize or drop a qualification.

    Literal marker keys cause a full-format fallback, so source text cannot be
    confused with a generated reference. Small/non-beneficial inputs stay full.
    """
    def collision(node):
        if isinstance(node, dict):
            return MARKER in node or any(collision(v) for v in node.values())
        return isinstance(node, list) and any(collision(v) for v in node)

    full = {"format": "full", "content": value}
    if collision(value):
        return full
    seen = {}

    def visit(node, pointer):
        serialized = canonical(node)
        if len(serialized.encode("utf-8")) >= minimum_bytes:
            if serialized in seen:
                return {MARKER: seen[serialized]}
            seen[serialized] = pointer
        if isinstance(node, dict):
            keys = sorted(node, key=lambda k: (PRIORITY.index(k) if k in PRIORITY else len(PRIORITY), k))
            return {k: visit(node[k], pointer + "/" + k.replace("~", "~0").replace("/", "~1")) for k in keys}
        if isinstance(node, list):
            return [visit(v, pointer + "/" + str(i)) for i, v in enumerate(node)]
        return node

    reduced = {"format": FORMAT, "content": visit(value, "#")}
    return reduced if len(canonical(reduced).encode("utf-8")) < len(canonical(full).encode("utf-8")) else full


def decode(encoded):
    """Reconstruct a recorded prompt payload for evidence/recovery audits."""
    root = encoded["content"]
    if encoded["format"] == "full":
        return root
    if encoded["format"] != FORMAT:
        raise ValueError("Unknown context encoding")

    def expand(node, trail):
        if isinstance(node, dict) and set(node) == {MARKER}:
            pointer = node[MARKER]
            if not isinstance(pointer, str) or not pointer.startswith("#/") or pointer in trail:
                raise ValueError("Invalid or cyclic context reference")
            target = root
            try:
                for key in pointer[2:].split("/"):
                    key = key.replace("~1", "/").replace("~0", "~")
                    target = target[int(key)] if isinstance(target, list) else target[key]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ValueError("Missing context reference") from exc
            return expand(target, trail | {pointer})
        if isinstance(node, dict):
            return {k: expand(v, trail) for k, v in node.items()}
        if isinstance(node, list):
            return [expand(v, trail) for v in node]
        return node

    return expand(root, set())
