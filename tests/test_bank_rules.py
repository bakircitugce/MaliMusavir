from app.bank import BankMatchingRule, suggest_account_from_description


def test_bank_rule_suggestion_matches_turkish_description() -> None:
    suggestions = suggest_account_from_description(
        "SGK prim ödemesi - İstanbul",
        [
            BankMatchingRule(
                name="sgk_payment",
                keywords=("SGK", "Sosyal Güvenlik"),
                account_code="361.01.001",
                account_name="Ödenecek SGK",
            )
        ],
    )

    assert len(suggestions) == 1
    assert suggestions[0].account_code == "361.01.001"
    assert suggestions[0].matched_keywords == ("SGK",)
    assert suggestions[0].requires_review is True


def test_bank_rule_suggestions_prioritize_more_keyword_matches() -> None:
    suggestions = suggest_account_from_description(
        "Kredi kartı POS komisyonu",
        [
            BankMatchingRule("pos", ("POS",), "108.01.001", "POS"),
            BankMatchingRule(
                "pos_commission",
                ("POS", "komisyon"),
                "770.03.001",
                "Banka POS Komisyonu",
            ),
        ],
    )

    assert [item.rule_name for item in suggestions] == ["pos_commission", "pos"]


def test_bank_rule_suggestions_ignore_empty_description() -> None:
    assert suggest_account_from_description("   ", []) == []
