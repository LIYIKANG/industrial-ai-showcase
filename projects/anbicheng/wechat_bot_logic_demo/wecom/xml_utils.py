"""企业微信 XML 消息解析与构造。"""

import time
import xml.etree.ElementTree as ET


def _find_text(root, name, default=""):
    """安全读取 XML 节点文本。"""
    node = root.find(name)
    if node is None or node.text is None:
        return default
    return node.text


def _cdata(value):
    """生成 CDATA 内容，兼容文本里极少见的 ]]>。"""
    text = "" if value is None else str(value)
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def parse_plain_message(xml_text):
    """解析企业微信解密后的明文 XML。"""
    root = ET.fromstring(xml_text)
    return {
        "to_user_name": _find_text(root, "ToUserName"),
        "from_user_name": _find_text(root, "FromUserName"),
        "create_time": _find_text(root, "CreateTime"),
        "msg_type": _find_text(root, "MsgType"),
        "content": _find_text(root, "Content"),
        "msg_id": _find_text(root, "MsgId"),
        "agent_id": _find_text(root, "AgentID"),
    }


def build_text_reply(to_user_name, from_user_name, content, agent_id=""):
    """构造企业微信被动文本回复明文 XML。"""
    agent_xml = f"<AgentID>{agent_id}</AgentID>" if agent_id else ""
    return (
        "<xml>"
        f"<ToUserName>{_cdata(to_user_name)}</ToUserName>"
        f"<FromUserName>{_cdata(from_user_name)}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        f"<MsgType>{_cdata('text')}</MsgType>"
        f"<Content>{_cdata(content)}</Content>"
        f"{agent_xml}"
        "</xml>"
    )
