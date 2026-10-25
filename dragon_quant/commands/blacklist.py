"""blacklist 命令：板块黑名单（拉取领涨/领跌板块时过滤）管理。"""

from __future__ import annotations


def handle(args, services):
    """板块黑名单管理。"""
    from dragon_quant.storage import db
    action = getattr(args, "blacklist_action", None)
    if action == "add":
        db.add_sector_blacklist(args.name)
        print(f"✅ 已加入黑名单: {args.name}")
    elif action == "remove":
        db.remove_sector_blacklist(args.name)
        print(f"✅ 已移除黑名单: {args.name}")
    else:  # list / 默认
        names = db.get_sector_blacklist()
        if names:
            print(f"板块黑名单（{len(names)} 个）:")
            for n in names:
                print(f"  - {n}")
        else:
            print("黑名单为空")
