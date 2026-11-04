from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class BankMatchingRule:
    name: str
    keywords: tuple[str, ...]
    account_code: str
    account_name: str


@dataclass(frozen=True)
class BankRuleSuggestion:
    rule_name: str
    account_code: str
    account_name: str
    matched_keywords: tuple[str, ...]
    requires_review: bool = True


def _normalize(value: str) -> str:
    replacements = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.sub(r"[^a-z0-9]+", " ", value.translate(replacements).lower())


def suggest_account_from_description(
    description: str,
    rules: Iterable[BankMatchingRule],
) -> list[BankRuleSuggestion]:
    normalized_description = _normalize(description)
    if not normalized_description.strip():
        return []
    description_words = normalized_description.split()
    suggestions: list[BankRuleSuggestion] = []
    for rule in rules:
        matched_keywords = tuple(
            keyword
            for keyword in rule.keywords
            if any(
                word == _normalize(keyword).strip()
                or word.startswith(_normalize(keyword).strip())
                for word in description_words
            )
        )
        if matched_keywords:
            suggestions.append(
                BankRuleSuggestion(
                    rule_name=rule.name,
                    account_code=rule.account_code,
                    account_name=rule.account_name,
                    matched_keywords=matched_keywords,
                )
            )
    suggestions.sort(key=lambda item: (-len(item.matched_keywords), item.rule_name))
    return suggestions
