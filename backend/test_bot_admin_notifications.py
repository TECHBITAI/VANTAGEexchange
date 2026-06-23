import bot


def test_build_admin_notification_text_contains_transaction_details():
    msg = bot.build_admin_notification_text(
        tx_id=7,
        user_name='Ahmed',
        username='ahmed',
        tx_type='BUY',
        amount=100.0,
        total_with_fee=103.0,
        network='BEP20',
        wallet_address='0xabc',
        payment_method='Kuraimi',
        payment_info='حساب 123',
        tx_hash_or_code='hash123',
        action='📬 طلب شراء جديد',
    )

    assert 'طلب رقم #7' in msg
    assert 'Ahmed' in msg
    assert 'BUY' in msg
    assert 'BEP20' in msg
    assert 'hash123' in msg
    assert 'طلب شراء جديد' in msg


def test_build_admin_action_markup_includes_edit_button():
    markup = bot.build_admin_action_markup(tx_id=7, user_id=99, tx_type='BUY')
    texts = [button.text for row in markup.inline_keyboard for button in row]
    assert any('تعديل' in text for text in texts)


def test_normalize_telegram_target_extracts_group_slug_from_invite_link():
    assert bot.normalize_telegram_target('https://t.me/+JWJo1MIGc082YzU0') == '+JWJo1MIGc082YzU0'


def test_get_admin_target_prefers_group_link_over_default_chat_id(monkeypatch):
    monkeypatch.setattr(bot, 'GROUP_LINK', 'https://t.me/+JWJo1MIGc082YzU0')
    monkeypatch.setattr(bot, 'ADMIN_CHAT_ID', '123456789')
    monkeypatch.setattr(bot, 'RESOLVED_ADMIN_CHAT_ID', None)
    assert bot.get_admin_target() == 'https://t.me/+JWJo1MIGc082YzU0'
