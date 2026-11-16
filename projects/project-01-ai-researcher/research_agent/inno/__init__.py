import os as _os
import re as _re


def _normalize_no_proxy():
    # 修复（2026-11-10）：Windows 上 NO_PROXY 常含方括号 IPv6 字面量（如
    # `localhost,127.0.0.1,[::1],::1`）。httpx 0.27.2 在构造 AsyncClient(trust_env=True)
    # 时会把每个 no-proxy 条目当成 URL 模式解析，`[::1]` 被误读成端口 ':1]' 抛
    # InvalidURL。litellm 在 `import litellm` 时就构造这样一个客户端，于是
    # `from .core import MetaChain` 连带整个 research_agent.inno 包导入即崩溃，
    # 流水线还没开始跑就挂（与模型余额无关）。实测裸 `::1`、CIDR `127.0.0.0/8`
    # 都能被 httpx 接受，只有方括号形式不行。这里把 `[IPv6]` 归一化成裸 IPv6，
    # 保留“本地地址不走代理”的原意，只去掉 httpx 解析不了的方括号。
    try:
        for _key in ("NO_PROXY", "no_proxy"):
            _val = _os.environ.get(_key)
            if not _val:
                continue
            _parts = [p.strip() for p in _val.split(",")]
            _fixed = [_re.sub(r"^\[([0-9A-Fa-f:]+)\]$", r"\1", p) for p in _parts]
            _new = ",".join(_fixed)
            if _new != _val:
                _os.environ[_key] = _new
    except Exception:
        # 兜底：任何异常都不应阻断包导入
        pass


_normalize_no_proxy()

from .core import MetaChain
from .types import Agent, Response
__all__ = ["MetaChain", "Agent", "Response"]