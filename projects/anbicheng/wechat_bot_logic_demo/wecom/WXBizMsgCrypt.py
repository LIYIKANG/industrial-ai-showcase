"""企业微信 WXBizMsgCrypt Python 3 实现。

接口名保留官方 WXBizMsgCrypt.py 的 VerifyURL、DecryptMsg、EncryptMsg。
AES 依赖 pycryptodome：pip install pycryptodome。
"""

import base64
import hashlib
import os
import struct
import time
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES


class WXBizMsgCryptErrorCode:
    """企业微信回调加解密错误码。"""

    OK = 0
    ValidateSignatureError = -40001
    ParseXmlError = -40002
    ComputeSignatureError = -40003
    IllegalAesKey = -40004
    ValidateCorpidError = -40005
    EncryptAESError = -40006
    DecryptAESError = -40007
    IllegalBuffer = -40008
    EncodeBase64Error = -40009
    DecodeBase64Error = -40010
    GenReturnXmlError = -40011


class PKCS7Encoder:
    """提供基于 32 字节块的 PKCS#7 padding。"""

    block_size = 32

    @classmethod
    def encode(cls, text):
        """补齐待加密字节。"""
        amount_to_pad = cls.block_size - (len(text) % cls.block_size)
        if amount_to_pad == 0:
            amount_to_pad = cls.block_size
        return text + bytes([amount_to_pad]) * amount_to_pad

    @classmethod
    def decode(cls, decrypted):
        """去除解密后的 padding。"""
        if not decrypted:
            raise ValueError("empty decrypted data")

        pad = decrypted[-1]
        if pad < 1 or pad > cls.block_size:
            raise ValueError("invalid padding")
        return decrypted[:-pad]


def _sha1_signature(token, timestamp, nonce, encrypted_text):
    """按照企业微信规则生成消息签名。"""
    sort_list = [token, timestamp, nonce, encrypted_text]
    sort_list.sort()
    raw = "".join(sort_list).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _safe_text(value):
    """把 None 转为空字符串，便于签名和 XML 拼接。"""
    return "" if value is None else str(value)


class WXBizMsgCrypt:
    """封装企业微信回调 URL 验证、消息解密、被动回复加密。"""

    def __init__(self, sToken, sEncodingAESKey, sReceiveId):
        self.token = _safe_text(sToken)
        self.encoding_aes_key = _safe_text(sEncodingAESKey)
        self.receive_id = _safe_text(sReceiveId)

        try:
            self.key = base64.b64decode(self.encoding_aes_key + "=")
        except Exception as exc:  # noqa: BLE001 - 与官方错误码接口保持一致
            raise ValueError("EncodingAESKey base64 解码失败") from exc

        if len(self.key) != 32:
            raise ValueError("EncodingAESKey 解码后的 AESKey 必须是 32 字节")

        self.iv = self.key[:16]

    def VerifyURL(self, sMsgSignature, sTimeStamp, sNonce, sEchoStr):
        """验证 URL，并返回 echostr 解密后的明文。"""
        signature = _sha1_signature(
            self.token,
            _safe_text(sTimeStamp),
            _safe_text(sNonce),
            _safe_text(sEchoStr),
        )
        if signature != _safe_text(sMsgSignature):
            return WXBizMsgCryptErrorCode.ValidateSignatureError, None

        return self._decrypt(sEchoStr)

    def DecryptMsg(self, sPostData, sMsgSignature, sTimeStamp, sNonce):
        """解密企业微信 POST 消息，返回明文 XML。"""
        try:
            xml_tree = ET.fromstring(sPostData)
            encrypt_node = xml_tree.find("Encrypt")
            if encrypt_node is None or encrypt_node.text is None:
                return WXBizMsgCryptErrorCode.ParseXmlError, None
            encrypted_text = encrypt_node.text
        except ET.ParseError:
            return WXBizMsgCryptErrorCode.ParseXmlError, None

        signature = _sha1_signature(
            self.token,
            _safe_text(sTimeStamp),
            _safe_text(sNonce),
            encrypted_text,
        )
        if signature != _safe_text(sMsgSignature):
            return WXBizMsgCryptErrorCode.ValidateSignatureError, None

        return self._decrypt(encrypted_text)

    def EncryptMsg(self, sReplyMsg, sNonce, sTimeStamp=None):
        """加密被动回复 XML，并生成企业微信要求的密文回包。"""
        timestamp = _safe_text(sTimeStamp or int(time.time()))
        nonce = _safe_text(sNonce)

        ret, encrypted_text = self._encrypt(sReplyMsg)
        if ret != WXBizMsgCryptErrorCode.OK:
            return ret, None

        try:
            signature = _sha1_signature(self.token, timestamp, nonce, encrypted_text)
            encrypted_xml = (
                "<xml>"
                f"<Encrypt><![CDATA[{encrypted_text}]]></Encrypt>"
                f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
                f"<TimeStamp>{timestamp}</TimeStamp>"
                f"<Nonce><![CDATA[{nonce}]]></Nonce>"
                "</xml>"
            )
            return WXBizMsgCryptErrorCode.OK, encrypted_xml
        except Exception:
            return WXBizMsgCryptErrorCode.GenReturnXmlError, None

    def _encrypt(self, plain_text):
        """AES-CBC 加密明文 XML。"""
        try:
            plain_bytes = _safe_text(plain_text).encode("utf-8")
            random_bytes = os.urandom(16)
            msg_len = struct.pack("!I", len(plain_bytes))
            receive_id = self.receive_id.encode("utf-8")
            raw = random_bytes + msg_len + plain_bytes + receive_id
            padded = PKCS7Encoder.encode(raw)
            cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
            encrypted = cipher.encrypt(padded)
            return (
                WXBizMsgCryptErrorCode.OK,
                base64.b64encode(encrypted).decode("utf-8"),
            )
        except Exception:
            return WXBizMsgCryptErrorCode.EncryptAESError, None

    def _decrypt(self, encrypted_text):
        """AES-CBC 解密密文，返回明文 XML 或 echostr。"""
        try:
            encrypted_bytes = base64.b64decode(_safe_text(encrypted_text))
        except Exception:
            return WXBizMsgCryptErrorCode.DecodeBase64Error, None

        try:
            cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
            decrypted = cipher.decrypt(encrypted_bytes)
            plain = PKCS7Encoder.decode(decrypted)
        except Exception:
            return WXBizMsgCryptErrorCode.DecryptAESError, None

        try:
            msg_len = struct.unpack("!I", plain[16:20])[0]
            msg = plain[20 : 20 + msg_len]
            receive_id = plain[20 + msg_len :].decode("utf-8")
        except Exception:
            return WXBizMsgCryptErrorCode.IllegalBuffer, None

        if self.receive_id and receive_id != self.receive_id:
            return WXBizMsgCryptErrorCode.ValidateCorpidError, None

        return WXBizMsgCryptErrorCode.OK, msg.decode("utf-8")
