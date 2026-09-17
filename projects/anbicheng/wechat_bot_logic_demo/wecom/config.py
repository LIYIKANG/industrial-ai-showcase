"""企业微信配置读取。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WeComConfig:
    """企业微信自建应用配置。"""

    corp_id: str
    token: str
    encoding_aes_key: str
    agent_id: str = ""
    secret: str = ""

    @classmethod
    def from_env(cls):
        """从环境变量读取企业微信配置。"""
        return cls(
            corp_id=os.getenv("WECOM_CORP_ID", "").strip(),
            token=os.getenv("WECOM_TOKEN", "").strip(),
            encoding_aes_key=os.getenv("WECOM_ENCODING_AES_KEY", "").strip(),
            agent_id=os.getenv("WECOM_AGENT_ID", "").strip(),
            secret=os.getenv("WECOM_SECRET", "").strip(),
        )

    def validate_callback_config(self):
        """校验回调加解密必需配置。"""
        missing = []
        if not self.corp_id:
            missing.append("WECOM_CORP_ID")
        if not self.token:
            missing.append("WECOM_TOKEN")
        if not self.encoding_aes_key:
            missing.append("WECOM_ENCODING_AES_KEY")

        if missing:
            raise ValueError(f"缺少企业微信回调配置：{', '.join(missing)}")

        if len(self.encoding_aes_key) != 43:
            raise ValueError("WECOM_ENCODING_AES_KEY 长度必须是 43 位")
