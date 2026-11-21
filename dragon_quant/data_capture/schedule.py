"""宿主调度模板（只输出配置，绝不安装）。

模板说明：macOS 用 launchd、Linux 用 systemd timer、其余用 cron；三者都只是
**建议编排**，安装后才会真正定时执行。模板本身不联网、不写库，也不代表系统
定时器已经启用。是否安装需用户另行授权。
"""

from __future__ import annotations

from typing import Optional

SCHEDULE_KINDS = ("launchd", "systemd", "cron")

_PLACEHOLDER = "<WORKDIR>"

_LAUNCHD = """<?xml version="1.0" encoding="UTF-8"?>
<!-- dragon-quant 竞价采集模板：仅示例，未安装。安装前请确认 WORKDIR/解释器路径。-->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.dragonquant.capture.auction</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python</string>
    <string>-m</string>
    <string>dragon_quant</string>
    <string>assets</string>
    <string>capture</string>
    <string>session</string>
    <string>--phase</string>
    <string>auction</string>
    <string>--json</string>
  </array>
  <key>WorkingDirectory</key><string>__WORKDIR__</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>26</integer></dict>
  <key>StandardOutPath</key><string>__WORKDIR__/capture-auction.log</string>
  <key>StandardErrorPath</key><string>__WORKDIR__/capture-auction.err.log</string>
</dict>
</plist>
"""

_SYSTEMD = """# dragon-quant 竞价采集模板：仅示例，未安装。
# systemctl --user daemon-reload && systemctl --user enable --now dragonquant-capture.timer
[Unit]
Description=dragon-quant auction capture
[Service]
Type=oneshot
WorkingDirectory=__WORKDIR__
ExecStart=/usr/bin/env python -m dragon_quant assets capture session --phase auction --json
[Install]
WantedBy=default.target

# ---- dragonquant-capture.timer ----
# [Unit]
# Description=dragon-quant auction capture timer
# [Timer]
# OnCalendar=*-*-* 09:26:00
# Persistent=true
# [Install]
# WantedBy=timers.target
"""

_CRON = """# dragon-quant 竞价采集模板：仅示例，未安装（crontab -e 后才会生效）。
# 机器睡眠/关机期间不会执行；错过的窗口在 coverage 中显示为 missed。
26 9 * * 1-5  cd __WORKDIR__ && /usr/bin/env python -m dragon_quant assets capture session --phase auction --json
"""

_TEMPLATES = {"launchd": _LAUNCHD, "systemd": _SYSTEMD, "cron": _CRON}


def render_schedule(kind: str = "launchd", *,
                    workdir: Optional[str] = None) -> dict:
    """返回 ``{"kind", "installed": False, "content"}``；从不安装。"""
    if kind not in _TEMPLATES:
        raise ValueError(f"未知调度模板: {kind}（可选 {SCHEDULE_KINDS}）")
    directory = workdir or _PLACEHOLDER
    content = _TEMPLATES[kind].replace("__WORKDIR__", directory)
    return {
        "kind": kind,
        "installed": False,
        "workdir": directory,
        "note": "仅模板输出；安装系统定时器需用户另行授权",
        "content": content,
    }
