"""机器绑定加密（P4/N59，chuan/security/binding.py）测试。

注意：所有加解密用例都显式注入 fingerprint，不依赖真实机器指纹——
确保 CI/换机后测试仍稳定（PBKDF2 400k 迭代在真实指纹上约几十 ms）。
"""

from __future__ import annotations

import numpy as np
import pytest

from chuan.security import binding

FP_A = ["mac:00:11:22:33:44:55", "host:box-a", "sys:Windows-AMD64-11"]
FP_B = ["mac:00:11:22:33:44:55", "host:box-b", "sys:Windows-AMD64-11"]


# ── 指纹与密钥派生 ──────────────────────────────────


def test_fingerprint_deterministic() -> None:
    assert binding.machine_fingerprint(FP_A) == binding.machine_fingerprint(FP_A)


def test_fingerprint_differs_on_component_change() -> None:
    assert binding.machine_fingerprint(FP_A) != binding.machine_fingerprint(FP_B)


def test_fingerprint_is_sha256_hex() -> None:
    fp = binding.machine_fingerprint(FP_A)
    assert len(fp) == 64
    int(fp, 16)  # 合法 hex


def test_derive_key_stable_and_32_bytes() -> None:
    fp = binding.machine_fingerprint(FP_A)
    assert len(binding.derive_key(fp)) == 32
    assert binding.derive_key(fp) == binding.derive_key(fp)


def test_derive_key_differs_across_machines() -> None:
    assert binding.derive_key(binding.machine_fingerprint(FP_A)) != binding.derive_key(
        binding.machine_fingerprint(FP_B)
    )


# ── 加解密往返 ──────────────────────────────────────


def test_encrypt_decrypt_text_roundtrip() -> None:
    fp = binding.machine_fingerprint(FP_A)
    token = binding.encrypt_text("hello 川流 secret", fp)
    assert token is not None
    assert binding.is_binding_token(token)
    assert binding.decrypt_text(token, fp) == "hello 川流 secret"


def test_encrypt_decrypt_bytes_roundtrip() -> None:
    fp = binding.machine_fingerprint(FP_A)
    data = bytes(range(256))
    token = binding.encrypt_bytes(data, fp)
    assert token is not None
    assert binding.decrypt_bytes(token, fp) == data


def test_encrypt_output_not_plaintext() -> None:
    fp = binding.machine_fingerprint(FP_A)
    token = binding.encrypt_text("plaintext-content", fp)
    assert token is not None
    assert "plaintext-content" not in token
    assert "plaintext" not in token


# ── 换机/换盘 → 解密失败（核心语义：机器绑定） ────────


def test_decrypt_fails_on_other_machine() -> None:
    token = binding.encrypt_text("secret", binding.machine_fingerprint(FP_A))
    assert token is not None
    # 换机器（FP_B）→ 密钥失配 → None
    assert binding.decrypt_text(token, binding.machine_fingerprint(FP_B)) is None
    assert binding.decrypt_bytes(token, binding.machine_fingerprint(FP_B)) is None


def test_same_component_different_mac_still_fails() -> None:
    """换机但只换一个组件 → 指纹变 → 解密失败（绑定是整体指纹）。"""
    fp_a = binding.machine_fingerprint(FP_A)
    fp_a2 = binding.machine_fingerprint(FP_A[:-1] + ["host:box-a2"])
    token = binding.encrypt_text("secret", fp_a)
    assert binding.decrypt_text(token, fp_a2) is None


# ── 异常输入静默降级 ─────────────────────────────────


def test_decrypt_non_token_returns_none() -> None:
    assert binding.decrypt_text("not-a-token", binding.machine_fingerprint(FP_A)) is None
    assert binding.decrypt_bytes(None, binding.machine_fingerprint(FP_A)) is None  # type: ignore[arg-type]
    assert binding.decrypt_bytes(b"CHUANBIND1:garbage", binding.machine_fingerprint(FP_A)) is None


def test_decrypt_corrupted_token_returns_none() -> None:
    fp = binding.machine_fingerprint(FP_A)
    token = binding.encrypt_text("secret", fp)
    assert token is not None
    corrupted = token[:-4] + "AAAA"  # 篡改尾部 base64
    assert binding.decrypt_text(corrupted, fp) is None


def test_encrypt_never_raises_on_bad_input() -> None:
    assert binding.encrypt_bytes(None) is None  # type: ignore[arg-type]
    assert binding.encrypt_bytes(12345) is None  # type: ignore[arg-type]


def test_is_binding_token() -> None:
    assert binding.is_binding_token("CHUANBIND1:abc") is True
    assert binding.is_binding_token("CHUANBIND1:") is True
    assert binding.is_binding_token("plain") is False
    assert binding.is_binding_token(123) is False
    assert binding.is_binding_token(None) is False


# ── 文件级封装 ──────────────────────────────────────


def test_encrypt_decrypt_file(tmp_path) -> None:
    fp = binding.machine_fingerprint(FP_A)
    p = tmp_path / "secret.txt"
    p.write_bytes(b"line1\nline2")  # 二进制写，避免 Windows 自动 \r\n
    assert binding.encrypt_file(p, fp) is True
    # 落盘后不应是明文
    raw = p.read_text(encoding="utf-8")
    assert "line1" not in raw
    assert binding.decrypt_file(p, fp) == b"line1\nline2"


def test_decrypt_file_missing_returns_none(tmp_path) -> None:
    assert binding.decrypt_file(tmp_path / "nope.bin", binding.machine_fingerprint(FP_A)) is None


def test_encrypt_file_failure_returns_false(tmp_path) -> None:
    assert binding.encrypt_file(tmp_path / "no_such_dir" / "x.bin") is False
