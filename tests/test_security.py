import unittest

from mousebridge.transport.security import (
    host_allowed,
    make_challenge,
    sign_challenge,
    verify_challenge,
)


class ChallengeTest(unittest.TestCase):
    def test_sign_and_verify(self) -> None:
        nonce = make_challenge()
        digest = sign_challenge("minha-chave", nonce)
        self.assertTrue(verify_challenge("minha-chave", nonce, digest))

    def test_wrong_key_fails(self) -> None:
        nonce = make_challenge()
        digest = sign_challenge("chave-a", nonce)
        self.assertFalse(verify_challenge("chave-b", nonce, digest))

    def test_wrong_nonce_fails(self) -> None:
        digest = sign_challenge("chave", make_challenge())
        self.assertFalse(verify_challenge("chave", make_challenge(), digest))

    def test_nonces_are_unique(self) -> None:
        self.assertNotEqual(make_challenge(), make_challenge())

    def test_tampered_digest_fails(self) -> None:
        nonce = make_challenge()
        digest = sign_challenge("chave", nonce)
        tampered = ("0" if digest[0] != "0" else "1") + digest[1:]
        self.assertFalse(verify_challenge("chave", nonce, tampered))


class HostAllowedTest(unittest.TestCase):
    def test_empty_list_allows_all(self) -> None:
        self.assertTrue(host_allowed("10.0.0.7", ()))

    def test_exact_match(self) -> None:
        allowed = ("192.168.1.42",)
        self.assertTrue(host_allowed("192.168.1.42", allowed))
        self.assertFalse(host_allowed("192.168.1.43", allowed))

    def test_wildcard_prefix(self) -> None:
        allowed = ("192.168.1.*",)
        self.assertTrue(host_allowed("192.168.1.99", allowed))
        self.assertFalse(host_allowed("192.168.2.99", allowed))

    def test_multiple_patterns(self) -> None:
        allowed = ("10.0.0.5", "192.168.0.*")
        self.assertTrue(host_allowed("10.0.0.5", allowed))
        self.assertTrue(host_allowed("192.168.0.20", allowed))
        self.assertFalse(host_allowed("172.16.0.1", allowed))


if __name__ == "__main__":
    unittest.main()
