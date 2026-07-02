"""Log and report sanitizer - removes sensitive information from outputs."""
from __future__ import annotations

import re
from typing import Any


# Default built-in sensitive patterns
_BUILTIN_PATTERNS: list[tuple[re.Pattern, str]] = [
    # token/auth/cookie -> keep key, replace value
    (re.compile(r'((?:token|authorization|cookie|auth_token|access_token)[\s:=]+)([\w\-./+]+)', re.IGNORECASE), r'\1***'),
    # Chinese mobile numbers: 1xx xxxx xxxx
    (re.compile(r'(1[3-9]\d)(\d{4})(\d{4})'), r'\1****\4'),
    # Email: user@domain -> us***@domain
    (re.compile(r'([\w.]+)(@[\w.]+)'), lambda m: m.group(1)[:2] + '***' + m.group(2)),
    # ID card: 18 digits, keep last 4
    (re.compile(r'\b(\d{14})(\d{3}[\dXx])\b'), r'**************\2'),
]


class Sanitizer:
    """
    Text sanitizer that redacts sensitive patterns from strings.
    """

    def __init__(
        self,
        enabled: bool = True,
        custom_patterns: list[str] | None = None,
    ):
        self.enabled = enabled
        self._patterns = list(_BUILTIN_PATTERNS)
        if custom_patterns:
            for p in custom_patterns:
                try:
                    compiled = re.compile(p)
                    self._patterns.append((compiled, '***'))
                except re.error:
                    pass

    def sanitize(self, text: str) -> str:
        """Sanitize text by applying all redaction rules."""
        if not self.enabled or not text:
            return text
        result = text
        for pattern, replacement in self._patterns:
            try:
                if callable(replacement):
                    result = pattern.sub(replacement, result)
                else:
                    result = pattern.sub(replacement, result)
            except Exception:
                pass
        return result

    def sanitize_dict(self, d: dict) -> dict:
        """Recursively sanitize all string values in a dict."""
        if not self.enabled:
            return d
        result = {}
        for k, v in d.items():
            if isinstance(v, str):
                result[k] = self.sanitize(v)
            elif isinstance(v, dict):
                result[k] = self.sanitize_dict(v)
            elif isinstance(v, list):
                result[k] = [self.sanitize(x) if isinstance(x, str) else x for x in v]
            else:
                result[k] = v
        return result
