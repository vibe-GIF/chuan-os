"""机器绑定加密（P4 安全增强 / N59，ADR-057）。

借鉴 Aivy「灵魂数据加密到硬件指纹」：chuan-os 此前明文 SQLite/JSON，本模块提供
「数据绑定到本机硬件」的加密——数据拷到别的机器 / 换系统盘后解密失败，不可读。

方案（确定性 + 优雅降级）：
- 机器指纹：网卡 MAC（uuid.getnode）+ 主机名（platform.node）+ 平台信息 +
  系统盘卷序列号（Windows 用 ctypes `GetVolumeInformationW`，格式化不变，
  是「机器绑定」的核心锚点；非 Windows 用 MAC+hostname 兜底）拼成指纹串。
- 密钥派生：PBKDF2-HMAC-SHA256（hashlib 标准库，40 万次迭代）从指纹串派生
  32 字节密钥——指纹一样密钥才一样，换机即密钥失配。
- 加密：优先 `cryptography.Fernet`（AES-128-CBC + HMAC，正规加密，cryptography
  在 langchain 生态普遍已装）；不可用时回退标准库方案（HMAC 派生流密钥 XOR +
  SHA-256 MAC 完整性校验，仅防「拷走不可读」而非高强度密码分析）。
- 输出带版本头 ``CHUANBIND1:`` + base64；解密失败（换机/换盘/数据损坏）返回
  None，绝不抛错（对齐项目「失败静默降级」惯例）。

默认关闭（config security.binding.enabled: false）。开启后新写数据可加密落盘；
旧明文数据不兼容时由调用方决定如何处理（decrypt 失败 → None）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import platform
import struct
import uuid
from pathlib import Path

# cryptography 是首选加密后端（langchain 生态常见依赖）；缺失时回退标准库。
try:  # pragma: no cover - 探测性导入
    from cryptography.fernet import Fernet

    _HAS_FERNET = True
except Exception:  # noqa: BLE001 - 缺 cryptography 走标准库回退
    _HAS_FERNET = False

_HEADER = "CHUANBIND1:"
_PBKDF2_ITER = 400_000
_KEY_LEN = 32

# 版本头内子方案标识：F=Fernet、X=标准库 XOR 回退
_SCHEME_FERNET = b"F"
_SCHEME_XOR = b"X"


def _fingerprint_components() -> list[str]:
    """采集机器指纹原始组件（可被测试注入覆盖的列表）。

    Windows 下补系统盘卷序列号（8 位十六进制，格式化不变），是「换机/换盘
    即失效」的关键锚点；采集失败跳过该组件（不阻断整体指纹）。
    """
    comps: list[str] = []
    try:
        comps.append(f"mac:{uuid.getnode():012x}")
    except Exception:  # noqa: BLE001
        pass
    try:
        comps.append(f"host:{platform.node()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        comps.append(f"sys:{platform.system()}-{platform.machine()}-{platform.release()}")
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            vol = ctypes.create_unicode_buffer(32)
            if ctypes.windll.kernel32.GetVolumeInformationW(
                wintypes.LPCWSTR("C:\\"),
                vol,
                len(vol),
                None,
                None,
                None,
                None,
                0,
            ):
                comps.append(f"vol:{vol.value.strip()}")
        except Exception:  # noqa: BLE001 - 卷序列号采集失败不影响指纹
            pass
    return comps


def machine_fingerprint(components: list[str] | None = None) -> str:
    """计算机器指纹字符串（SHA-256 hex）。

    默认采集本机；``components`` 可注入（测试用），传入后不再采集真实组件。
    """
    parts = components if components is not None else _fingerprint_components()
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive_key(fingerprint: str, salt: bytes = b"chuan-binding-v1") -> bytes:
    """从指纹派生 32 字节对称密钥（PBKDF2-HMAC-SHA256）。

    指纹相同 → 密钥相同（本机可解密）；指纹不同（换机/换盘）→ 密钥不同。
    """
    return hashlib.pbkdf2_hmac(
        "sha256", fingerprint.encode("utf-8"), salt, _PBKDF2_ITER, dklen=_KEY_LEN
    )


def _xor_encrypt(key: bytes, data: bytes) -> bytes:
    """标准库回退：HMAC-SHA256 派生流密钥 XOR + SHA-256 MAC 校验。

    输出 ``iv(16) + mac(32) + ciphertext``。仅防「数据拷走不可读」，不作高强度
    密码学承诺（cryptography 可用时走 Fernet）。
    """
    iv = os.urandom(16)
    keystream = hashlib.sha256(key + iv).digest()
    while len(keystream) < len(data):
        keystream += hashlib.sha256(keystream).digest()
    ct = bytes(b ^ k for b, k in zip(data, keystream))
    mac = hmac.new(key, iv + ct, hashlib.sha256).digest()
    return iv + mac + ct


def _xor_decrypt(key: bytes, blob: bytes) -> bytes:
    iv, mac, ct = blob[:16], blob[16:48], blob[48:]
    expected = hmac.new(key, iv + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("binding mac mismatch")
    keystream = hashlib.sha256(key + iv).digest()
    while len(keystream) < len(ct):
        keystream += hashlib.sha256(keystream).digest()
    return bytes(b ^ k for b, k in zip(ct, keystream))


def encrypt_bytes(data: bytes, fingerprint: str | None = None) -> str | None:
    """加密 bytes → ``CHUANBIND1:`` 前缀字符串。任何失败返回 None。"""
    try:
        key = derive_key(fingerprint or machine_fingerprint())
        if _HAS_FERNET:
            token = Fernet(base64.urlsafe_b64encode(key)).encrypt(data)
            payload = _SCHEME_FERNET + token
        else:
            payload = _SCHEME_XOR + _xor_encrypt(key, data)
        return _HEADER + base64.b64encode(payload).decode("ascii")
    except Exception:  # noqa: BLE001 - 加密失败静默降级
        return None


def decrypt_bytes(token: str, fingerprint: str | None = None) -> bytes | None:
    """解密 ``CHUANBIND1:`` 字符串 → bytes。非绑定格式 / 换机 / 损坏 → None。"""
    try:
        if not isinstance(token, str) or not token.startswith(_HEADER):
            return None
        payload = base64.b64decode(token[len(_HEADER):])
        key = derive_key(fingerprint or machine_fingerprint())
        scheme, body = payload[:1], payload[1:]
        if scheme == _SCHEME_FERNET and _HAS_FERNET:
            return Fernet(base64.urlsafe_b64encode(key)).decrypt(body)
        if scheme == _SCHEME_XOR:
            return _xor_decrypt(key, body)
        return None
    except Exception:  # noqa: BLE001 - 任何失败（含换机密钥失配）静默返回 None
        return None


def encrypt_text(text: str, fingerprint: str | None = None) -> str | None:
    """加密文本 → 绑定 token 字符串。"""
    return encrypt_bytes(text.encode("utf-8"), fingerprint)


def decrypt_text(token: str, fingerprint: str | None = None) -> str | None:
    """解密绑定 token → 文本；失败返回 None。"""
    raw = decrypt_bytes(token, fingerprint)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def is_binding_token(value: object) -> bool:
    """判断字符串是否是本模块生成的绑定 token（非空、带版本头）。"""
    return isinstance(value, str) and value.startswith(_HEADER)


def encrypt_file(path: str | Path, fingerprint: str | None = None) -> bool:
    """把文件内容加密重写为绑定 token 文本文件（原地）。成功返回 True。"""
    try:
        p = Path(path)
        data = p.read_bytes()
        token = encrypt_bytes(data, fingerprint)
        if token is None:
            return False
        p.write_text(token, encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def decrypt_file(path: str | Path, fingerprint: str | None = None) -> bytes | None:
    """读取绑定 token 文本文件并解密为原始 bytes；失败返回 None。"""
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
        return decrypt_bytes(token, fingerprint)
    except Exception:  # noqa: BLE001
        return None
