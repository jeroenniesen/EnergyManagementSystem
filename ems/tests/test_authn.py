from ems.authn import dummy_verify, hash_password, hash_token, new_token, verify_password


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert h != "correct horse"
    assert h.startswith("$argon2")
    assert verify_password(h, "correct horse") is True
    assert verify_password(h, "wrong") is False


def test_verify_bad_hash_returns_false_not_raise():
    assert verify_password("not-a-hash", "x") is False


def test_dummy_verify_does_not_raise():
    dummy_verify()


def test_tokens_random_and_hash_stable():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 32
    assert hash_token(a) == hash_token(a)
    assert hash_token(a) != hash_token(b)
    assert len(hash_token(a)) == 64  # sha256 hex
