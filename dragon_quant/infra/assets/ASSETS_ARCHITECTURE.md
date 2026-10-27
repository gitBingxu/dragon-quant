# 数据资产架构（prep / auction / review-account / assets 同步）

> 目标架构图：数据资产的**采集 → 物化 → 消费 → 云端同步**全链路。
> 图末标注了当前实现状态与目标架构的差异。

## 1. 总体组件图

```mermaid
flowchart TB
    subgraph Internet["互联网数据源"]
        NET["TDX / 同花顺 / 雪球 / 腾讯 / 财联社 / 巨潮"]
    end

    subgraph Modules["功能模块"]
        PREP["prep 模块<br>盘后复盘"]
        AUCTION["auction 模块<br>集合竞价"]
        RA["review-account / buy<br>策略与信号"]
        SYNC["assets 同步模块<br>infra/assets/sync"]
    end

    subgraph LocalDB["本地数据库 (assets.db)"]
        direction TB
        subgraph RawAssets["原始数据资产层"]
            PA["prep_assets"]
            AA["auction_assets"]
        end
        subgraph Snapshots["算法快照层"]
            PS["prep_snapshots<br>(主线/情绪/候选池)"]
            AS["auction_snapshots<br>(竞价强度)"]
        end
    end

    subgraph Cloud["云端资产"]
        R2["R2 / B2 对象存储<br>blobs/ + manifests/"]
    end

    NET -->|"① 爬取"| PREP
    NET -->|"① 爬取"| AUCTION

    PREP -->|"② 写入原始资产"| PA
    PA -->|"③ 读回"| PREP
    PREP -->|"④ 算法计算后写入"| PS

    AUCTION -->|"② 写入原始资产"| AA
    AA -->|"③ 读回"| AUCTION
    AUCTION -->|"④ 算法计算后写入"| AS

    PS -->|"⑤ 读取快照"| RA
    AS -->|"⑤ 读取快照"| RA
    RA -.->|"⑥ 快照缺失时：<br>读资产重算并回写"| PA
    RA -.->|"⑥ 快照缺失时：<br>读资产重算并回写"| AA
    PA -.->|"⑥ 重算结果回写"| PS
    AA -.->|"⑥ 重算结果回写"| AS

    SYNC <-->|"⑦ push / pull"| PA
    SYNC <-->|"⑦ push / pull"| AA
    SYNC <-->|"⑦ push / pull"| R2
```

## 2. prep / auction 两段式执行流

两个模块结构同构：**采集段**（触网，写原始资产）与**计算段**（纯函数，资产 → 快照）分离；`--recompute` 跳过采集段直接用已存资产重放算法。

```mermaid
flowchart LR
    subgraph 采集段["采集段（触网）"]
        C1["按注册表逐类采集<br>ASSET_COLLECTORS"]
        C2["单事务 UPSERT"]
    end
    subgraph DB1["assets.db"]
        RAW["prep_assets /<br>auction_assets"]
        SNAP["prep_snapshots /<br>auction_snapshots"]
    end
    subgraph 计算段["计算段（不触网）"]
        F1["从资产表读回"]
        F2["算法计算<br>（快照 = 资产纯函数）"]
        F3["写入快照表"]
    end

    C1 --> C2 --> RAW
    RAW --> F1 --> F2 --> F3 --> SNAP
```

`prep` 默认**增量采集**：先读存量资产 + `collection_report` 做健康判定——全部健康则零采集
（含 dragons 本地读取）直接进入计算段；否则只补采缺失/不健康且可采的资产（`dragons` 重采且
候选 code 集合变化时级联补采 `fund_flow`/`cninfo_announcements`）。`collection_report` 的语义是
「该日期资产的当前最佳已知状态」：只按键合并本次实际执行的 collector 条目，零采集时完全不动，
补采失败不覆盖既有健康状态（记 `last_attempt`）。`--recompute` 保持零检测零采集，直接用存量资产重放计算段。

## 3. review-account / buy 消费流（含快照缺失回退）

```mermaid
flowchart TD
    START["review-account / buy / auction 启动"] --> READ{"读 T-1 prep_snapshots<br>+ 当日 auction_snapshots<br>(仅竞价开关开启时)"}
    READ -->|"有值"| RULES["结合内置规则判定<br>标的 + 仓位"]
    READ -->|"无值"| FALLBACK["从 prep_assets /<br>auction_assets 读原始资产"]
    FALLBACK --> COMPUTE["调用 prep/auction 计算段<br>重算快照"]
    COMPUTE --> WRITE["快照回写 assets.db"]
    WRITE --> RULES
    RULES --> SIGNAL["输出买入信号 / 仓位"]
```

## 4. assets 双向同步

本地 db 是**权威副本**，云端只作异地备份（原始资产不可再生：`same_day_only` 与 `auction` 仅当日可取）。

```mermaid
flowchart LR
    subgraph Local["本地 assets.db"]
        LA["prep_assets /<br>auction_assets"]
    end
    subgraph Sync["infra/assets/sync"]
        CODEC["codec<br>规范化 JSON + gzip + sha256"]
        MANIFEST["manifest<br>每日一份，重采覆盖"]
    end
    subgraph CloudStore["云端 (S3 兼容)"]
        BLOB["blobs/&lt;date&gt;/<br>&lt;source&gt;_&lt;type&gt;.&lt;sha256&gt;.json.gz"]
        MF["manifests/&lt;date&gt;.json"]
    end

    LA -->|"push"| CODEC --> BLOB
    CODEC --> MANIFEST --> MF
    BLOB -->|"pull"| CODEC
    MF --> MANIFEST
    CODEC -->|"恢复"| LA
```

---

## 当前实现状态对照

| 图中能力 | 状态 |
|----------|------|
| prep / auction 两段式（资产 → 快照纯函数、`--recompute`） | ✅ 已实现 |
| review-account / buy / auction 读 `prep_snapshots`（候选池 + 情绪仓位） | ✅ 已实现（`market.prep_snapshot_for` / `collect_candidates_ex` / `load_market_sentiment`）；严格 T-1，三路径共用 |
| prep 推荐集为空时回退 legacy | ✅ 已实现（`fallback_reason=all_ignored`；回退池不再应用 ignore 出池；P 按快照 verdict 标注） |
| 快照缺失时**读资产重算回写后继续**（图 3 的 ⑥ 路径） | ⚠️ 目标设计；当前实现是回退「3 日真龙并集」旧逻辑（`_legacy_candidates`），不会触发重算回写 |
| review-account / buy 消费 `auction_snapshots` | ✅ 已接线（`assets_db.list_auction_snapshots`）：`auction_ranking_enabled` / `auction_veto_enabled` 任一开启才读取；`SEMANTICS_VERIFIED=False` 期间 unverified → A 恒 Unknown |
| `auction` 采集池改为 prep 推荐 | ✅ 默认 `--pool prep`；空推荐集/缺失回退 legacy；`--pool legacy` 可复现旧口径 |
| assets 同步 push / pull / status / verify | ⚠️ M1 仅 `doctor`（连通性门槛）；push/pull 在 M2-M4 |
