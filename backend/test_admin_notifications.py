from backend.main import build_transaction_admin_message


def test_build_transaction_admin_message_includes_request_details_and_action():
    msg = build_transaction_admin_message(
        tx_id=12,
        user_id=12345,
        tx_type='BUY',
        amount=100.0,
        total_with_fee=103.0,
        network='BEP20',
        wallet_address='0xabc',
        payment_method='Kuraimi',
        payment_info='حساب 2325013',
        tx_hash_or_code='0xhash',
        action='موافقة',
    )

    assert 'طلب رقم #12' in msg
    assert 'BUY' in msg
    assert '12345' in msg
    assert 'BEP20' in msg
    assert '0xabc' in msg
    assert 'Kuraimi' in msg
    assert 'موافقة' in msg
