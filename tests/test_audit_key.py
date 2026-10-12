from RH_ComfyUI.utils.audit_key import mask_api_key, is_masked_api_key


def test_mask_api_key_head_tail() -> None:
    assert mask_api_key("") == ""
    assert mask_api_key("   ") == ""
    assert mask_api_key("abcd") == "abcd*****"
    assert mask_api_key("123456789") == "12345*****"
    assert mask_api_key("abcdefghij") == "abcde*****fghij"
    assert mask_api_key("sampleKEYSHOULDNOTLEAK12") == "sampl*****EAK12"
    assert "SHOULD" not in mask_api_key("sampleKEYSHOULDNOTLEAK12")


def test_mask_api_key_is_idempotent() -> None:
    masked = mask_api_key("sk-abcdefghijklmnopqrstuvwxyz")
    assert masked == "sk-ab*****vwxyz"
    assert mask_api_key(masked) == masked
    assert is_masked_api_key(masked) is True
    assert is_masked_api_key("sk-abc") is False
