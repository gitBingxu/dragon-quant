"""presenters：dashboard 展示规则纯投影（方案 §2.8 / §3）。

- 只做「领域 payload → 视图模型」的纯函数投影：无 I/O、无 SQL、无 provider；
- 三态、合法豁免、补名、跨日 delta、分组统计等规则集中在此，可单测；
- 路由只负责参数解析与响应模型，web_ui 其余部分不重复这些规则。
"""

from __future__ import annotations

__all__: list[str] = []
