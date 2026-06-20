# 评分器重构技术方案 —「识别真龙」五维评分体系

> 本方案以核心文案（龙头战法视频转写）为主，资金承接性维度的实现参考旧代码。
> 范围仅限「识别真龙」，不含买点/卖点（B/S）逻辑。

---

## 一、设计哲学（来自文案）

文案核心论断：**龙头不是预判出来的，是「识别」出来的。市场先选出龙头，操盘手只在第一时间识别市场的选择。**

因此评分器的定位是 **「识别器」而非「预测器」**：

- 输入：某一题材爆发日的候选股 + 板块上下文
- 输出：该股在四大特征上的得分 + 是否符合「真龙」画像的综合判定
- 不做任何对未来走势的预测，只对「当下盘面是否呈现真龙特征」打分

文案原文铁律："真龙必须是**同时满足**四大特征的" → 决定聚合方式必须是 **硬门槛 + 加权**（任一维度低于门槛即一票否决，不能靠其他维度补偿）。

---

## 二、五维评分器（四大特征 + 资金承接）

文案明确列出真龙的四大特征（带动性 / 领涨性 / 抗跌性 / 流动性），逐一拆解为可计算维度；并在此基础上**额外补充一个「资金承接性」维度**（文案未直接点名，用来衡量龙头所处板块整体是否强劲，实现参考旧代码）。

### 1. 带动性 Drive（权重 30%）
> 文案："它一涨停整个板块的小弟全都跟风高潮 / 总龙头必须能够带动全场的情绪"
> 识别手段（文案）："观察哪支股票最先涨停并且稳定的封死，同时观察同板块的个股是否能够跟随他拉升"

衡量：**当日**龙头是否最先封板、是否带动板块、板块小弟是否跟风高潮。三个子因子直接对应 txt：

- **封板最早**：在同板块涨停股封板池中，本股封板时点越早得分越高（"最先涨停并且稳定封死"）
- **带动板块**：本股是否领先板块拉升——有「个股先拉、板块随后跟」的动作即得分，否则不得分（"带动全场"）
- **板块共鸣**：板块内涨停占比越高得分越高；板块内涨幅 >3% 的小弟越多得分越高（"小弟全都跟风高潮"）
- 数据：`sector:components:{s}` + `quotes:batch`（板块涨停/上涨家数）、`kline:1min:{code}`（本股封板时点 + 带动时序）、`kline:1min` of 同板块涨停股（封板池对比）、`kline:1min:sector:{s}`（带动时序基准，**1分粒度**）

### 2. 领涨性 Leadership（权重 25%）
> 文案："龙头通常都是板块里面连板数量最多的、涨幅最大的股票，对板块内其他小弟在空间上有领涨优势"

衡量：在板块内的空间高度领先程度（连板最多 + 涨幅最大）。两点直接对应 txt：

- **连板最多**：该股连板天数在板块内的领先程度（"连板数量最多"）
- **涨幅最大**：该股 5 日总涨幅在板块内的领先程度（"涨幅最大的股票"、"空间上有领涨优势"）
- 数据：候选池 `Candidate.board_count`（连板高度）+ `Candidate.fived_pct`（5日总涨幅，**需 Phase C 新增**）+ `sector:components:{s}`（板块内其他成分股横向对比）

> 连板高度与 5 日总涨幅均由编排器 Phase C 从日K算好写入 `Candidate`，评分器直接消费，自身不回看历史日K。

### 3. 抗跌性 AntiDrop（权重 15%）
> 文案："大盘跳水时它能横盘稳住，大盘一旦企稳它第一个起飞 / 扛住分歧不死，谓之真龙"

衡量：相对**大盘**与**所处板块**的双重抗跌韧性。

- **逆势稳定**：大盘下跌区间内该股的相对回撤（跌得少 = 高分）
- **率先反弹**：大盘企稳后该股是否第一个由跌转涨（反弹领先时序）
- **板块同步抗跌**：所处板块跳水时该股是否比板块更扛跌（板块1分K 对照）
- 数据：`kline:day:{code}` + `kline:1min:000001`（大盘当日分时，**必须**）+ `kline:1min:sector:{s}`（板块当日1分分时基准）+ `kline:1min:{code}`（当日日内承接）

### 4. 流动性 Liquidity（权重 20%）
> 文案："没有换手、全是一字板顶板的装死，是走不远的 / 真龙必须是流动性换手走出来的焦点 / 流动性最充沛的载体"

衡量：换手是否充沛、封板是否扎实。**文案单列的第四大特征，独立成维**（不并入资金承接）。

- **换手充沛度**：换手率水平及其在板块内的相对高低（换手越充沛，分歧承接越强）
- **封板质量**：封单强度（封单量相对成交/流通盘）+ 封板稳定性（封死 vs 反复开板），呼应文案"稳定封死、封单最大"
- 数据：`kline:day:{code}`（换手/振幅）+ `quotes:batch`（换手/量比/**封单量**，板块内相对化）+ `kline:1min:{code}`（封板稳定性）

> 封单数据源（**已实测定稿**）：雪球 `pankou.json` 盘后返回空体（`Content-Length:0`），而扫描在盘后运行，故**弃用 pankou，改用腾讯 gtimg 收盘盘口快照**——`quotes:batch` 已在拉取，gtimg `f[10]`=买一量（手）盘后仍保留收盘瞬间状态；涨停股收盘卖盘清空、买一量即封单量。零新增请求，且与分母 `Quote.volume`（f[36]，手）同源同单位，天然满足单位铁律。`Quote` 模型新增 `bid1_price/bid1_volume/ask1_volume` 字段承载盘口。

> 注：**不设一字板惩罚**——能一字封死也是强势表现（封单大、买盘汹涌）。流动性维只奖励「换手充沛 + 封板扎实」，不因一字而扣分。

### 5. 资金承接性 Absorption（权重 10%）
> 文案无直接对应；属于龙头识别的补充盘面证据。实现思想参考旧 `scorers/absorption.py`，在 `scorers_v2/absorption.py` 中新写（旧文件不改）。

衡量：**市场恐慌/调整时，其他板块的资金是否被虹吸到目标板块**——龙头所在板块能否成为全场资金的承接载体。

- **虹吸事件检测**：滑动窗口（30 分钟 / 6 根 5分K）扫描「目标板块拉升 + 其他板块同步或前移 5 分钟跳水」的因果事件
  - 目标板块窗口：涨幅 > 0.3% 且 ≥4/6 根阳线
  - 其他板块：同窗口或前移一根 bar 的窗口跌幅 < -0.3%，受影响板块数 ≥ 2
  - 因果约束：其他板块跳水不晚于目标板块首次拉升，时间差 ≤ 10 分钟且同一交易日
  - 持续性约束：目标板块窗口回撤比例 ≤ 0.3
- **事件打分（三维）**：虹吸强度 40% + 广度 20% + 持续性 40%；多事件叠加给 bonus（每多一个 +5，上限 15）
- **降级**：无显著虹吸信号或板块 5分K 不足时给中性分（50），并在 details 标注 fallback
- 数据：`目标板块5分K` + `其他全部板块5分K` + `板块名称映射`

---

## 三、聚合策略：门槛 + 加权

文案"同时满足四大特征" → 四大特征设硬门槛，资金承接仅作加权贡献，不否决 → 两段式：

```
Step 1 硬门槛（一票否决，仅四大特征）
  drive / leadership / anti_drop / liquidity 四维各设最低门槛 floor_i。
  任一维 score_i < floor_i → is_true_dragon=False（出局，不进入加权）。
  - 资金承接性「不」作为硬性门槛：板块强劲与否只影响综合分高低，不直接否决真龙身份。
  - 不设一字板否决：能一字封死也是强势，流动性维只奖励不惩罚。

Step 2 加权综合
  通过四大特征门槛者（资金承接无论高低均进入加权）：
  composite = 0.30*drive + 0.25*leadership + 0.15*antidrop + 0.20*liquidity + 0.10*absorption
  按 composite 降序得到真龙排名。
```

> 门槛值与权重均集中在 config 常量（见第八节阈值表），便于调参回测，不写死在算法里。

---

## 四、模块结构（重构后）

> **重要：本次重构全部落在全新目录 `scorers_v2/`，旧 `scorers/` 目录及其所有评分器、旧编排调用路径原样保留、零改动。** 新旧并存、互不影响，便于灰度对比与回滚。新旧切换由编排器/CLI 通过开关选择走 `scorers`（旧）还是 `scorers_v2`（新）。

```
scorers_v2/                # 全新目录，与旧 scorers/ 并存
├── base.py            # 抽象基类 + ScoreResult/DragonVerdict 数据模型 + 共享工具
├── drive.py           # 带动性 (30%)
├── leadership.py      # 领涨性 (25%)
├── anti_drop.py       # 抗跌性 (15%)
├── liquidity.py       # 流动性 (20%)   ← 文案第四特征，独立维度
├── absorption.py      # 资金承接性 (10%)  ← 跨板块虹吸检测，实现参考旧 scorers/absorption.py
├── registry.py        # 评分器注册表（插件式，便于增删维度/调权重）
└── aggregator.py      # 门槛+加权聚合器，产出 DragonVerdict
```

### 统一接口约定
评分器是 **cache 消费者**：所有 provider 在 orchestrator Phase A→D 拉完数据后写入内存 `DataCache`，scorer 只通过 `cache.get(key)` 读取，不直接发请求。沿用既有签名：

```python
def score(code: str, cache: DataCache, **kwargs) -> ScoreResult:
    """
    cache 中可用数据（粒度：个股/大盘/板块 当日1分K + 板块10日5分K + 日K + 盘口）：
      - kline:day:{code}            个股日K            list[KBar]
      - kline:day:000001            上证指数日K（大盘基准）
      - kline:1min:{code}           个股当日1分K（雪球 minute.json，仅当日、仅top_n）
      - kline:1min:000001           上证指数当日1分K（大盘当日分时基准）
      - kline:1min:sector:{s_code}  板块当日1分K（同花顺 /v6/time 原始1分分时，不聚合）
      - kline:5min:sector:{s_code}  板块近10日5分K（同花顺 /v6/line 历史，仅 absorption 用）
      - sector:components:{s_code}  板块成分股        list[StockInfo]
      - quotes:batch                批量行情(含收盘盘口 bid1/ask1)  dict[code→Quote]
      - __meta__:sector_codes       领跌板块代码列表   list[str]
      - __meta__:sector_name_map    板块代码→名称      dict[str,str]

    各维额外 kwargs:
      - drive:      candidate_pool, primary_sector
      - leadership: primary_sector
      - anti_drop:  primary_sector（板块跳水判定用）
      - liquidity:  primary_sector
      - absorption: primary_sector, all_sector_codes, sector_name_map
    返回 ScoreResult(dim, score[0-100], weight, details)
    """
```

> **粒度铁律**：当日盘中时序对比（带动性、抗跌性）一律 **1分K**——个股取雪球 `minute.json` 的 1分K，板块取同花顺 `/v6/time` 的原始 1分分时（不再降采样为5分），大盘取雪球 `minute.json("000001")` 1分K，三者同 1分粒度可直接对齐。**仅资金承接** 需要回看10日历史，用同花顺 `/v6/line` 的 5分OHLC（历史回看无需1分精度，数据量更可控）。

### 输出数据模型
```python
@dataclass
class DragonVerdict:
    code: str
    is_true_dragon: bool        # 是否通过五维门槛
    composite: float            # 加权综合分 0-100
    rank: int | None            # 真龙池内排名
    dims: dict[str, ScoreResult]  # 五维独立分（drive/leadership/anti_drop/liquidity/absorption）
    reject_reason: str | None   # 若被否决，说明卡在哪一维
```

每维 0-100 独立可读 + 综合分 + 真龙判定（对应用户选择：多维分+综合分）。

---

## 五、数据来源与接口映射

评分器只依赖 `DataCache`，由 orchestrator Phase A→D 通过各 provider 预填充。下表是每个 scorer 实际读取的缓存键及其上游 provider 接口：

| 缓存键 | 上游 Provider.接口 | 数据内容 | 被哪些维度用 |
|--------|-------------------|---------|------------|
| `sector:components:{s_code}` | `ths.get_sector_components(code, page=1)` | 板块成分股 + 当日涨跌幅快照 `StockInfo` | drive / leadership |
| `kline:1min:{code}` | `xueqiu.get_minute_kline(code)`（仅当日） | 个股当日1分K | drive(封板时点+带动时序) / anti_drop(盘中横盘/反弹) / liquidity(封板稳定性) |
| `kline:1min:000001` | `xueqiu.get_minute_kline("000001")`（仅当日，**新增**） | 大盘当日1分K | anti_drop(大盘跳水/企稳判定) |
| `kline:1min:sector:{s_code}` | `ths.get_sector_1min_kline(code)`（当日原始1分分时，**新增**，不聚合） | 板块当日1分K | drive(带动时序基准) / anti_drop(板块跳水基准) |
| 板块近10日5分K（absorption 内部取，不入 `__meta__`） | `ths.get_sector_5min_kline_history(code, 10)`（**新增**，`/v6/line/.../30/last1000.js` 真实OHLC） | 板块近10日5分K | absorption(虹吸检测) |
| `pankou:{code}` | ~~`xueqiu.get_pankou`~~ **已弃用**（盘后空体） → 改由 `quotes:batch` 的 gtimg `bid1_volume`(f[10],手) 承载封单 | 收盘盘口封单 | liquidity(封板质量) |
| `quotes:batch` | `tencent.batch_get_quotes(codes[:200])`（含 `bid1_price/bid1_volume/ask1_volume` 盘口） | 全成分股实时行情+收盘盘口 `list[Quote]` | drive(板块共鸣) / liquidity(换手相对化+封单) |
| `Candidate.board_count` / `Candidate.fived_pct` | orchestrator Phase C（**fived_pct 新增**） | 连板高度 / 5日总涨幅 | leadership |
| `__meta__:sector_codes` | orchestrator 注入（领跌板块代码列表） | `list[str]` | absorption |
| `__meta__:sector_name_map` | orchestrator 注入（板块代码→名称） | `dict[str,str]` | absorption |

> 关键约束：
> - 雪球 `minute.json` / `pankou.json` **只返回当日/实时快照**——除资金承接外四维只作用于**当日盘面**。
> - 板块当日时序用同花顺 `/v6/time/` 的**原始 1分分时**（不再降采样 5分），与个股/大盘 1分K 同粒度；该接口本就返回 1分点位，旧 `get_sector_5min_kline` 是 provider 内主动聚合的结果。
> - 资金承接需回看 **10 个交易日** 的板块5分K，改用同花顺 K线路径 `/v6/line/` 取真实历史 OHLC（不是当日分时 `/v6/time/`）。
> - 连板高度/5日总涨幅由编排器算好透传，评分器不在内部回看历史日K。

---

## 六、各维度实现细节（接口 / 算法 / 缺口）

### 6.1 带动性 Drive（**仅当日盘面**）
- **txt 依据**："小弟全都跟风高潮" / "观察哪支股票最先涨停并且稳定封死，同时观察同板块个股是否能够跟随他拉升"
- **数据**：`sector:components:{s}` + `quotes:batch`（板块涨停/上涨家数）、`kline:1min:{code}`（本股封板时点 + 带动时序）、同板块涨停股的 `kline:1min`（封板池对比）、`kline:1min:sector:{s}`（带动时序基准，**1分粒度**）
- **算法**（只看扫描当日；**三个子因子各自满分 100，再按下方权重加权得 Drive 总分**）：
  1. **封板最早 `EARLY_W=0.40`**（子因子满分100）：在同板块涨停股「封板池」中，用各股当日1分K定位首次封死涨停的分钟时点，本股封得越早分越高。`s_early = (1 - 本股封板排名/池内涨停股数) × 100`；池内仅本股涨停=满分100；本股未封板=0
  2. **带动板块 `LEAD_W=0.35`（领先-跟随脉冲检测，子因子满分100）**：判定「本股拉升脉冲 → 板块随后跟涨，且板块未抢跑」。算法如下（个股/板块 1分K 已对齐到同一分钟轴，缺失分钟前向填充）：
     - **归一化涨幅曲线**（消量纲）：`g_s[t] = close_s[t]/preclose_s − 1`，`g_b[t] = close_b[t]/preclose_b − 1`
     - **① 识别个股拉升脉冲**：滑窗 `THRUST_WIN`（默认3分钟）算窗口净振幅 `Δh_s[t] = g_s[t] − g_s[t−w]`；当 `Δh_s[t] ≥ THRUST_PCT`（默认3%，3分钟至少拉3%才算脉冲）且为局部峰（相邻 w 内非极大抑制，避免一段拉升重复计数）→ 记脉冲起点 `t0 = t−w`，得脉冲集合 `T`。开盘跳空/一字高开（`g_s[0] ≥ THRUST_PCT`）记为 `t0=0` 脉冲
     - **② 板块跟随确认**：对每个 `t0`，板块跟随振幅 `Δh_b = max_{k∈[1,L]}(g_b[t0+k]) − g_b[t0]`（`L=LEAD_FOLLOW_BARS`）
     - **③ 因果方向（关键）**：满足两条才算一次「有效带动 `lead_event`」——
       - 跟随达标：`Δh_b ≥ SECTOR_FOLLOW_PCT`（板块体量大，阈值小，默认0.3%）
       - 板块未抢跑：`t0` 前 L 根内板块基本是平/跌的，即 `g_b[t0] − min_{k∈[1,L]}(g_b[t0−k]) < SECTOR_FOLLOW_PCT`
     - **④ 反向识别**：若板块先达标、个股后拉（板块抢跑）→ 记 `follow_event`（被带动，非带动）
     - **⑤ 打分（子因子 `s_lead`，0~100）**：设 `n_lead`=有效带动次数，`n_follow`=被带动次数。
       - `n_lead=0`（纯跟风票）→ **0 分**，`n_follow` 不再扣分（已触底）；若 `n_follow>0` 标注 `suspect_follower`
       - `n_lead≥1`：带动得分 `= 70 + (n_lead−1)×10`（每多一次带动 +10，封顶100）；叠加跟随扣分 `penalty = min(n_follow × 20, 100)`（每被带动一次 −20，最多扣100——龙头若不主动、被板块反拖就该罚）
       - `s_lead = clip(带动得分 − penalty + bonus, 0, 100)`
       - 注：`LEAD_FULL=4` 即 4 次带动达 100 分封顶（70+3×10）
     - **⑥ 整体方向增强（可选 bonus）**：取「开盘→个股首次封板前」区间的分钟收益率 `r_s/r_b`，时滞互相关 `ρ(τ)=corr(r_s[t], r_b[t+τ])`，`τ∈[0,L]`；若 `argmax_τ ρ(τ)=τ*>0` 且 `ρ(τ*)≥CORR_TH` → bonus=+`CORR_BONUS`（确认个股整体领先板块），否则 bonus=0
     - **降级**：板块1分K缺失/个股全程平线 → 中性偏低 + `degraded=True`
  3. **板块共鸣 `VOICE_W=0.25`**（子因子满分100）：板块内涨停占比 + 涨幅>`FOLLOW_PCT`的小弟占比。`s_voice = clip(涨停占比/VOICE_FULL,1)×60 + clip(强势占比/FOLLOW_FULL,1)×40`（满分100）
  4. **Drive 总分** = `s_early × EARLY_W + s_lead × LEAD_W + s_voice × VOICE_W`（`0.40+0.35+0.25=1.0`），clip[0,100]
- **缺口**：封板池需要**同板块涨停股的当日1分K**。现状 Phase D 只对 top_n 候选拉 1分K，板块内其他涨停股未必在候选池——需 Phase D 对「主板块成分股中当日涨停者」补拉 1分K（见第七节缺口）。
- **与旧逻辑差异**（旧 `scorers/drive.py` 保留不动，v2 不沿用其逻辑）：v2 不采用「近3个涨停日取最佳」「绝对时间阶梯」「连板加分」——txt 未提及历史统计；封板早晚改为「板块内当日封板池相对排名」，直接对应"最先涨停"。

### 6.2 领涨性 Leadership
- **txt 依据**："龙头通常都是板块里面连板数量最多的、涨幅最大的股票，对板块内其他小弟在空间上有领涨优势"
- **数据**：`Candidate.board_count`（连板高度）+ `Candidate.fived_pct`（5日总涨幅，**Phase C 新增**）+ `sector:components:{s}`（板块内其他成分股横向对比）
- **算法**（板块内横向对比；**两个子因子各自满分100，再各按50%加权**）：
  1. **连板最多 `BOARD_W=0.50`**（子因子满分100）：本股 `board_count` 与**板块内连板最高值** `B_max`（取该板块所有候选/成分股的最大连板）对比——
     - `s_board = clip(100 − (B_max − board_count) × BOARD_DECAY, 0, 100)`
     - 即本股=板块最高连板 → 100；每低 1 板扣 `BOARD_DECAY`（默认10）；低 10 板及以上 → 0
     - 边界：板块内仅本股有连板（`B_max=board_count`）→ 100；`board_count=0` 且板块也无连板 → `B_max=0`，差为0仍给100（首板齐发时不歧视，靠涨幅子因子区分）
  2. **涨幅最大 `PCT_W=0.50`**（子因子满分100）：本股 `fived_pct` 在**板块内当日涨停候选股**的 5 日总涨幅序列中的**百分位排名**——
     - **样本口径（已修正）**：取**板块内当日涨停候选股**（candidate_pool 中 `primary_sector` 相同者，去重含本股）的 `fived_pct`。**为何不用成分股**：`StockInfo.five_day_return` 仅对「当日拉过日K的涨停股」为真实值，其余几十只成分股为默认 0，混入会污染分位、把涨停股名次人为顶高；候选池的 `fived_pct` 均已在 Phase C 用真实日K算出，口径干净。
     - **打分（涨幅降序排名）**：把样本按 `fived_pct` 降序排名一次（`O(n log n)`），本股名次 `r`（涨幅最高者 `r=1`），样本数 `n`（=板块当日涨停股数）
       - `s_pct = (1 − r/n) × 100`
       - 涨幅第1名 → `(1−1/n)×100`；垫底 `r=n` → 0；`n=1 → s_pct=0`（单样本无相对优势，`degraded=True`）
     - 用板块内排名而非绝对涨幅，呼应 txt 的「板块内涨幅最大、空间领涨优势」语义
  3. **Leadership 总分** = `s_board × BOARD_W + s_pct × PCT_W`（`0.50+0.50=1.0`），clip[0,100]
- **缺口**：Phase C 需为每个 Candidate 计算并写入 `fived_pct`（5日总涨幅）。注：v2 路径下 Phase B 仅对「当日涨停候选股」拉日K，故 `Candidate.fived_pct` 仅对涨停候选有真实值——这正是 leadership 涨幅样本只用候选池的原因。
- **与旧逻辑差异**（旧 `scorers/leadership.py` 保留不动）：v2 不采用「历史5日 z-score / 正态CDF 分位」「lead-lag 加分」——改为直接用 5 日总涨幅在板块内排名；时序带动归入带动性维。

### 6.3 抗跌性 AntiDrop（**仅当日盘面**）
- **txt 依据**："大盘跳水时它能横盘稳住，大盘一旦企稳它第一个起飞 / 扛住分歧不死，谓之真龙"
- **数据**：`kline:1min:{code}`（个股当日分时）、`kline:1min:sector:{s}`（板块当日1分分时）、`kline:1min:000001`（大盘当日1分分时，见缺口）
- **算法**（当日盘中行为，非历史多日；**大盘维度、板块维度各自满分100，再加权**）。两维都用「同一个基准函数」`antidrop_vs(base_1min, stock_1min)`，只是基准 `base` 分别换成大盘/板块：
  - **归一化涨幅曲线**（消量纲，对齐到同一分钟轴）：`g_s[t]=close_s[t]/preclose_s−1`，`g_x[t]=close_x[t]/preclose_x−1`（x=大盘或板块）
  - **① 识别基准跳水段**：滑窗 `DIP_WIN`（默认 `=LEAD_FOLLOW_BARS=3` 分钟）算基准净跌幅 `Δd_x[t]=g_x[t]−g_x[t−w]`；将连续满足 `Δd_x < DIP_TH`（默认 −0.5%）的分钟合并为若干「跳水段」`[a,b]`（a=起跌点、b=段内最低点）。无跳水段 → 该维**中性分 65**（当日没考验，不奖不罚）
  - **② 横盘稳住（子项，0~100）**：对每个跳水段，基准在 `[a,b]` 的跌幅 `d_x = g_x[a]−g_x[b]`（>0），个股同区间跌幅 `d_s = g_s[a]−g_s[b]`；相对抗跌比 `ratio = d_s / max(d_x, ε)`
    - `s_hold_seg = clip((1 − ratio) × 100, 0, 100)`（个股不跌甚至翻红 `ratio≤0`→100；与基准同跌 `ratio=1`→0；跌得更狠→0）
    - 多段取按跌幅 `d_x` 加权平均 → `s_hold`
  - **③ 率先起飞（子项，0~100）**：取基准最低点 `b` 后的拐点（基准由跌转涨首根），看个股反弹领先——
    - 个股触底分钟 `tb_s` vs 基准触底分钟 `tb_x=b`：领先根数 `lead = clip(tb_x − tb_s, 0, REBOUND_LEAD_BARS)`（个股更早见底=领先）
    - 反弹幅度比：`b` 后 `REBOUND_LEAD_BARS` 根内个股回升 `up_s` 与基准回升 `up_x`，`amp = clip(up_s / max(up_x,ε), 0, 2)`
    - `s_rebound = clip( (lead/REBOUND_LEAD_BARS)×60 + (amp/2)×40, 0, 100)`（早见底给60、反弹更猛给40）
  - **④ 单维得分** `s_dim = s_hold × 0.6 + s_rebound × 0.4`（横盘稳住60% / 率先起飞40%）
  1. **大盘维度 `MARKET_W=0.6`**（满分100）：`s_market = antidrop_vs(大盘1min, 个股1min)`
  2. **板块维度 `SECTOR_W=0.4`**（满分100）：`s_sector = antidrop_vs(板块1min, 个股1min)`
  3. **AntiDrop 总分** = `s_market × MARKET_W + s_sector × SECTOR_W`（`0.6+0.4=1.0`），clip[0,100]
  - **降级**：大盘或板块1分K缺失 → 该维中性65 + `degraded=True`；两者皆缺 → 维中性分
- **缺口（新增）**：当日盘中判断需要**大盘1分K** 与 **板块1分K**——大盘当前缓存只有日K `kline:day:000001`，需新增 `xueqiu.get_minute_kline("000001")` → `kline:1min:000001`；板块需新增 `ths.get_sector_1min_kline(code)` → `kline:1min:sector:{s}`（同花顺 `/v6/time` 原始1分，不再聚合5分）。
- **与旧逻辑差异**（旧抗跌评分器保留不动）：v2 不采用「日K识别近30日跳水日、多日平均、连续跳水加成」——txt 讲的是当日盘中大盘跳水时的即时表现，非跨日统计。

### 6.4 流动性 Liquidity（新增独立维）
- **数据**：`kline:day:{code}`（振幅）、`quotes:batch`（换手率 `Quote.turnover_rate`（gtimg fields[38]，单位%）+ 当日成交量 `Quote.volume`（fields[36]，单位手）+ 收盘买一封单 `Quote.bid1_volume`（fields[10]，单位手），板块内相对化）、`kline:1min:{code}`（封板稳定性：盘中是否反复开板）
- **算法**（**两个子因子各自满分100，再各按50%加权**）：
  1. **换手充沛度 `TURNOVER_W=0.5`**（子因子满分100）：当日换手率绝对水平 + 板块内相对分位，两者各半——
     - **绝对分 `s_to_abs`**：换手率 `to`（%）映射，`s_to_abs = clip(to / TURNOVER_FULL, 1) × 100`（`TURNOVER_FULL` 默认15%，即换手≥15%给满分）
     - **相对分 `s_to_rel`**：本股 `to` 在板块成分股 `quotes:batch` 换手序列中按降序排名 `r`、样本 `n`（n≤50），`s_to_rel = (1 − r/n) × 100`（与领涨涨幅同口径）
     - `s_turnover = s_to_abs × 0.5 + s_to_rel × 0.5`
  2. **封板质量 `SEAL_W=0.5`**（子因子满分100）：封单强度 + 封板稳定性，两者各半——
     - **封单强度 `s_seal_strength`**：`strength = bid1_volume封单量 / 当日成交量`。**单位铁律**：封单 `Quote.bid1_volume`（gtimg fields[10]，单位「手」）与分母 `Quote.volume`（gtimg fields[36]，单位同为「手」，当日累计）**同源同单位**直接相除，天然无量级误差。`s_seal_strength = clip(strength / SEAL_STRENGTH_REF, 1) × 100`（`SEAL_STRENGTH_REF` 默认0.3）。未涨停（无有效封单/`bid1_volume`≈0）→ 0
     - **封板稳定性 `s_seal_stable`**：用当日1分K统计「触及涨停价后又回落（开板）」次数 `n_open`，映射：`n_open=0 → 100`、`1~2 → 60`、`≥3 → 20`
     - `s_seal = s_seal_strength × 0.5 + s_seal_stable × 0.5`
  3. **Liquidity 总分** = `s_turnover × TURNOVER_W + s_seal × SEAL_W`（`0.5+0.5=1.0`），clip[0,100]
  4. **不设一字板惩罚**：一字封死 → `n_open=0`（稳定性100）+ `bid1_volume` 通常极大（强度100）→ 封板质量满分；换手低导致换手充沛度偏低是客观反映，但封板质量补上，不额外扣分
  - **降级**：`bid1_volume` 缺失/为0 → 封单强度子项按"已涨停=60/未涨停=0"近似 + `degraded=True`；1分K缺失 → 稳定性给中性60 + 标注
- **封单数据方案（已实测定稿）**：
  - **弃用雪球 `pankou.json`**：盘后实测返回 `Content-Length:0` 空体，而扫描固定在盘后运行（交易时段被拦截），生产路径取不到封单。
  - **改用腾讯 gtimg 收盘盘口快照**：`batch_get_quotes` 已在拉取，gtimg 行情字段 `f[9]`=买一价、`f[10]`=买一量(手)、`f[19]`=卖一价、`f[20]`=卖一量(手)，盘后仍保留收盘瞬间状态。涨停股收盘时卖盘清空、买一量 `f[10]` 即封单量（手）。
  - 零新增请求、与分母同源同单位；`Quote` 模型新增 `bid1_price/bid1_volume/ask1_volume` 字段，`tencent._parse_gtimg_quote` 补解析 f[9]/f[10]/f[20]。

### 6.5 资金承接性 Absorption
- **数据**：`kline:5min:sector:{primary_sector}`（目标板块）、`kline:5min:sector:{其他全部板块}`（虹吸对手盘）、`__meta__:sector_codes`（领跌板块列表）、`__meta__:sector_name_map`（展示）
- **算法**（实现参考旧 `scorers/absorption.py`，在 `scorers_v2/absorption.py` 新写；回看窗口由 5 改为 **10 个交易日**）：
  1. 取最近 **10 个交易日**，按5分钟bucket对齐目标板块与其他板块时间轴
  2. 滑动窗口（6根=30分钟）检测虹吸事件：目标涨幅>0.3% 且 ≥4阳线；其他板块同窗口或前移1根跌幅<-0.3% 且受影响≥2个；回撤比例≤0.3；跳水不晚于拉升且时间差≤10分钟同日
  3. 单事件三维打分：虹吸强度40% + 广度20% + 持续性40%；多事件 bonus（每多1个+5，上限15）
     - **强度（正向口径）**：目标板块拉升越高 + 出逃规模越大 → 分越高。`强度 = clip(目标涨幅/TARGET_REF,1)×100 × 0.5 + clip(|出逃均跌|×出逃数/FLIGHT_REF,1)×100 × 0.5`（`TARGET_REF=2%`、`FLIGHT_REF=5`）。**注**：旧 absorption 的强度=目标涨幅/(出逃均跌×出逃数)，出逃越多反而稀释分母、压低强度；v2 改为出逃规模与拉升幅度同向加分。
  4. 无信号/数据不足 → 中性分50 + fallback 标注
- **板块历史5分K 方案（已调研，可行）**：当前 `get_sector_5min_kline` 用的 `d.10jqka.com.cn/v6/time/48_{inner}/last.js` 只返回当日分时，**无法回溯历史**。但同花顺另有 **K线路径** 可直接取多日历史5分K：
  - 路径：`https://d.10jqka.com.cn/v6/line/48_{inner}/30/last1000.js`（`30`=5分钟周期码，`60`=1分钟；`last1000`=最近1000条，5分K约20交易日，覆盖10日足够）
  - innerCode 复用现有 `_get_inner_code()`（885xxx），无需新映射
  - 返回 JSONP，`data` 字段每根逗号分隔为 `时间,开,高,低,收,量,额,...`，**直接是真实 OHLC**，省掉 `_aggregate_5min` 聚合，且比分时聚合更准
  - **无 Cookie、无 hexin-v 反爬**，可复用现有 `_curl` + `_parse_jsonp`（JSONP 回调名 `quotebridge_v6_line_...`，现有正则 `\((\{.*\})\)` 仍匹配）
  - 时间格式 `YYYYMMDDHHMM`，可直接定位最近10个交易日
- **唯一参考旧代码的维度**：除回看窗口由 5 改 10 外，事件检测与三维打分逻辑参照旧 `scorers/absorption.py` 在 v2 中重写（旧文件不动）。

> **数据范围铁律**：除资金承接外，其余四维评分器**只消费扫描当日数据**；资金承接消费**最近 10 个交易日**的板块 5分K。

---

## 七、接口缺口汇总

| 缺口 | 影响维度 | 现状 | 建议 |
|------|---------|------|------|
| **盘口封单量** | liquidity（封板质量） | `Quote` 无 bid/ask 字段 | **已实测定稿**：雪球 `pankou.json` 盘后空体不可用，改用腾讯 gtimg 收盘盘口——`Quote` 新增 `bid1_price/bid1_volume/ask1_volume`，`tencent._parse_gtimg_quote` 补解析 f[9]/f[10]/f[20]，封单 = `bid1_volume`（手）。**零新增请求**（复用 `quotes:batch`） |
| **大盘当日1分K** | anti_drop（盘中跳水/企稳） | 仅有大盘日K `kline:day:000001` | 新增 Phase C/D 调 `xueqiu.get_minute_kline("000001")` → `kline:1min:000001`（接口已存在） |
| **板块当日1分K** | drive / anti_drop（带动/板块跳水时序） | 仅有 `get_sector_5min_kline`（provider 内把原始1分主动聚合为5分） | 新增 `ths.get_sector_1min_kline(code)`：同花顺 `/v6/time/48_{inner}/last.js` 返回的本就是 1分分时，**跳过 `_aggregate_5min`** 直接返回 1分点位序列；缓存键 `kline:1min:sector:{s}`，复用 `_get_inner_code`+`_curl`+`_parse_jsonp` |
| **同板块涨停股当日1分K** | drive（封板最早） | Phase D 只对 top_n 候选拉 1分K | Phase D 对「主板块成分股中当日涨停者」补拉 `kline:1min`，构建板块封板池 |
| **板块历史5分K（10日）** | absorption（虹吸回看10日） | 同花顺 `/v6/time/.../last.js` 只返回当日分时 | **已调研可行**：改用同花顺 K线路径 `/v6/line/48_{inner}/30/last1000.js`（直接多日真实5分K OHLC，无 Cookie/反爬，innerCode 复用 `_get_inner_code`）；新增 `ths.get_sector_5min_kline_history(code, days)` |
| **5日总涨幅入候选池** | leadership（涨幅最大） | `StockInfo.five_day_return` 已在 Phase B 算，但未落到 Candidate | Phase C 写入 `Candidate.fived_pct`（复用已算值，无新接口） |
| **板块成分股样本偏小** | drive（板块共鸣分母 + 封板池范围） | `get_sector_components` 第1页仅10只；ajax 翻页被 hexin-v 反爬拦截（401），实际永远止于10只 | **已修复**：改用非 ajax 路径 `/gn/detail/order/desc/page/{p}/code/{code}/` 免登录翻页可到前**5页≈50只**（第6页302跳登录），v2 用 `all_pages=True` 扩到≈50只。注：**leadership 涨幅分位已不用成分股**（改用候选池 `fived_pct`，见 §6.2），成分股扩样本现仅服务 drive 板块共鸣的家数占比与封板池涨停股识别 |

> 全部缺口均有现成接口可补，无需引入新数据源。板块当日1分K 与 5分历史 都基于同花顺现有路径（`/v6/time` 原始1分、`/v6/line` 历史OHLC），仅 provider 内取/转处理不同。

---

## 八、关键计算细节备注

- **时序对齐**：带动性、抗跌性依赖「龙头动作 vs 板块/大盘动作」的时间先后，统一按 **1 分钟 bucket**（`timestamp // 60000`）对齐个股/板块/大盘 1分K，缺失分钟前向填充；**资金承接**回看10日历史，按 **5 分钟 bucket**（`timestamp // 300000`）对齐目标板块与其他板块。两类粒度分别对齐，不可混用。
- **板块内相对化**：领涨性/流动性的得分应是「板块内相对分位」而非绝对值，文案强调的是「最」（最先涨停、连板最多、流动性最充沛）。
- **一字板不惩罚**：能一字封死也是强势，流动性维只奖励「换手充沛 + 封板扎实」，不因一字扣分。
- **缺数据降级**：分时/盘口缺失时，对应子项降级为日K近似并在 details 标注 `degraded=True`，但不静默给 0 误伤。

---

## 九、阈值与权重表（推荐默认值，集中在 config，待确认）

> 以下为推荐初始值（部分参考旧代码经验值），全部集中到 `scorers_v2/registry.py` 常量，便于回测调参。**请逐项确认或修改。**

### 9.1 维度权重 & 门槛
| 维度 | 权重 | 门槛 floor（低于则一票否决） |
|------|------|------|
| drive 带动性 | 0.30 | 40 |
| leadership 领涨性 | 0.25 | 40 |
| anti_drop 抗跌性 | 0.15 | 35 |
| liquidity 流动性 | 0.20 | 35 |
| absorption 资金承接 | 0.10 | —（不设门槛，仅加权贡献） |

### 9.2 带动性 Drive
| 常量 | 含义 | 推荐值 |
|------|------|------|
| `LIMIT_UP_PCT` | 涨停判定阈值 | 9.9% |
| `EARLY_W` / `LEAD_W` / `VOICE_W` | 封板最早 / 带动板块 / 板块共鸣 子因子权重（各满分100加权，和=1.0） | 0.40 / 0.35 / 0.25 |
| `FOLLOW_PCT` | 板块共鸣中「强势小弟」涨幅下限 | 3.0% |
| `VOICE_FULL` | 板块共鸣满分对应的涨停家数占比 | 0.10 |
| `FOLLOW_FULL` | 板块共鸣满分对应的强势(>3%)家数占比 | 0.30 |
| 板块共鸣内部 | 涨停占比 / 强势占比 子权重 | 0.6 / 0.4 |
| `LEAD_FOLLOW_BARS` (L) | 带动板块：个股脉冲后板块跟随窗口 | 3 根（**1分K → 3分钟**） |
| `THRUST_WIN` (w) | 个股拉升脉冲滑窗 | 3 分钟 |
| `THRUST_PCT` | 个股脉冲净振幅 Δh_s 阈值（3分钟至少拉3%） | 3.0% |
| `SECTOR_FOLLOW_PCT` | 板块跟随振幅 Δh_b 达标阈值（兼作板块抢跑阈值） | 0.3% |
| 带动打分 | 首次带动基准 / 每多一次 +分 | 70 / +10（封顶100） |
| 跟随扣分 | 每次 follow_event 扣分 / 扣分上限（仅 n_lead≥1 时生效） | −20 / −100 |
| `LEAD_FULL` | 带动达 100 分的次数（70+3×10） | 4 次 |
| `CORR_TH` / `CORR_BONUS` | 时滞互相关确认阈值 / 领先 bonus | 0.5 / +10 |

### 9.3 领涨性 Leadership
| 常量 | 含义 | 推荐值 |
|------|------|------|
| `BOARD_W` / `PCT_W` | 连板最多 / 5日涨幅最大 子因子权重（各满分100加权，和=1.0） | 0.50 / 0.50 |
| `BOARD_DECAY` | 每低于板块最高连板 1 板的扣分（低10板及以上归0） | 10/板 |

### 9.4 抗跌性 AntiDrop
| 常量 | 含义 | 推荐值 |
|------|------|------|
| `DIP_TH` | 基准跳水段判定（1分K滑窗净跌幅） | -0.5% |
| `DIP_WIN` | 跳水段识别滑窗 | 3 分钟 |
| `MARKET_W` / `SECTOR_W` | 大盘维度 / 板块维度权重（各满分100加权，和=1.0） | 0.6 / 0.4 |
| 单维内部 | 横盘稳住 `s_hold` / 率先起飞 `s_rebound` 子权重 | 0.6 / 0.4 |
| 率先起飞内部 | 早见底（lead） / 反弹幅度（amp） 子权重 | 0.6 / 0.4 |
| `REBOUND_LEAD_BARS` | 企稳拐点后率先反弹判定窗口 | 3 根（**1分K → 3分钟**） |
| 无跳水段中性分 | 当日基准未跳水时该维给分 | 65 |

### 9.5 流动性 Liquidity
| 常量 | 含义 | 推荐值 |
|------|------|------|
| `TURNOVER_W` / `SEAL_W` | 换手充沛度 / 封板质量 子因子权重（各满分100加权，和=1.0） | 0.5 / 0.5 |
| 换手充沛度内部 | 绝对分 `s_to_abs` / 相对分 `s_to_rel` 子权重 | 0.5 / 0.5 |
| `TURNOVER_FULL` | 换手率绝对分满分阈值（换手≥此值给100） | 15% |
| 封板质量内部 | 封单强度 / 封板稳定性 子权重 | 0.5 / 0.5 |
| `SEAL_STRENGTH_REF` | 封单强度参考（bc1 / 当日成交量）满分阈值 | 0.3 |
| 封板稳定性 | 开板次数 0 / 1~2 / ≥3 映射 | 100 / 60 / 20 |

### 9.6 资金承接性 Absorption
| 常量 | 含义 | 推荐值 |
|------|------|------|
| `WINDOW` | 滑动窗口（5分K根数） | 6（30分钟） |
| `TARGET_MIN_UP` | 目标板块窗口涨幅下限 | 0.3% |
| `TARGET_MIN_YANG` | 目标板块窗口最少阳线数 | 4 |
| `DROP_TH` | 其他板块跳水阈值 | -0.3% |
| `MIN_AFFECTED` | 最少受影响板块数 | 2 |
| `MAX_DRAWDOWN_RATIO` | 目标板块窗口回撤比例上限 | 0.3 |
| `MAX_TIME_DIFF` | 跳水→拉升最大时间差 | 10 分钟 |
| `MAX_TRADE_DAYS` | 回看交易日数 | 10 |
| 事件三维权重 | 强度/广度/持续 | 0.40 / 0.20 / 0.40 |
| 强度内部权重 | 目标拉升分量 / 出逃规模分量 | 0.5 / 0.5 |
| `INT_TARGET_REF` | 目标拉升满分参考（窗口涨幅%） | 2.0% |
| `INT_FLIGHT_REF` | 出逃规模满分参考（\|均跌%\|×出逃数） | 5.0 |
| `MULTI_EVENT_BONUS` | 每多一个事件加分 / 上限 | 5 / 15 |

---

## 十、待实现文件清单

### 评分器（核心）—— 全部新建于 `scorers_v2/`，旧 `scorers/` 不动
| 文件 | 内容 |
|------|------|
| `scorers_v2/__init__.py` | 包初始化 |
| `scorers_v2/base.py` | ScoreResult / DragonVerdict 数据模型 + 共享工具（板块内分位、1分K对齐、5分bucket时序对齐） |
| `scorers_v2/drive.py` | 带动性算法 |
| `scorers_v2/leadership.py` | 领涨性算法 |
| `scorers_v2/anti_drop.py` | 抗跌性算法 |
| `scorers_v2/liquidity.py` | 流动性算法（新增独立维） |
| `scorers_v2/absorption.py` | 资金承接性算法（跨板块虹吸检测，实现参考旧 `scorers/absorption.py`，**新文件，不改旧文件**） |
| `scorers_v2/registry.py` | 维度注册表 + 权重/门槛配置常量 |
| `scorers_v2/aggregator.py` | 门槛+加权聚合 → DragonVerdict |

### 非评分器的编排/数据改动

> 原则：providers / models 的新增均为**纯增量**（新增方法/字段，不动旧签名）；编排器走 **v2 分支**（由开关选择），旧编排路径与旧 `scorers/` 调用保持不变。

| 文件 | 改动 |
|------|------|
| `models/types.py` | ① `Quote` 新增 `bid1_price`/`bid1_volume`/`ask1_volume` 字段（承载 gtimg 收盘盘口，**带默认值，纯增量**）；② `Candidate` 新增 `fived_pct`（5日总涨幅）字段（**纯增量，默认值不破坏旧用法**） |
| `providers/base.py` | 新增**普通方法**（默认 `raise NotImplementedError`，非 `@abstractmethod`，避免 eastmoney/tencent 实例化崩）：`get_sector_1min_kline(code)`、`get_sector_5min_kline_history(code, days)`（旧 `get_sector_5min_kline` 保留）。封单走 gtimg，**不新增 `get_pankou`** |
| `providers/tencent.py` | `_parse_gtimg_quote` 补解析盘口：`bid1_price=f[9]`、`bid1_volume=f[10]`(手)、`ask1_volume=f[20]`(手)，填入 `Quote` 新字段（**纯增量**） |
| `providers/ths.py` | ① 新增 `get_sector_1min_kline(code)`：调 `/v6/time/48_{inner}/last.js`，复用 `_get_inner_code`+`_curl`+`_parse_jsonp`，**跳过 `_aggregate_5min`** 直接把原始1分点位转 `list[KBar]`（单点位 → open=high=low=close）；② 新增 `get_sector_5min_kline_history(code, days)`：调 `/v6/line/48_{inner}/30/last1000.js`（**周期码 `30`=5分**，实测纠正），`_parse_jsonp` 后节点即顶层 dict，解析 `data` 行 `YYYYMMDDHHMM,开,高,低,收,量,额,...`（无 `pre`，pct 用前一根 close 推），截取最近 days 个交易日；③ 成分股翻页修复：新增 `PAGE_URL = /gn/detail/order/desc/page/{p}/code/{code}/`（**非 ajax**，免登录），`get_sector_components` 的 `all_pages` 分支由失效的 ajax `COMPONENTS_URL` 改走 `PAGE_URL`，`for p in range(2,6)` 翻到第5页（第6页302跳登录自然 break），稳定拿≈50只。**旧默认行为保持10只**。**旧 `get_sector_5min_kline` 原样保留** |
| **`orchestrator.py` v2 分支** | 新增一条 v2 扫描路径（开关切换），旧路径不动。v2 路径下：**Phase A** 领涨板块 `RANK_UP_COUNT` 用 5；**Phase B** 取每个领涨板块当日所有涨停个股为候选；**Phase C** 写 `Candidate.fived_pct`；**Phase D** 见下；最终调用 `scorers_v2` 聚合器而非旧 `scorers` |
| **`orchestrator.py` v2 Phase D 数据预填** | ① 封单走 gtimg 盘口（已含在 `quotes:batch`，**无需单独拉**）；② 拉大盘当日1分K `get_minute_kline("000001")` 写 `kline:1min:000001`；③ 对每个主板块拉 `get_sector_1min_kline` 写 `kline:1min:sector:{s}`（带动/抗跌时序基准）；④ 对「主板块成分股中当日涨停者」补拉 `kline:1min`（带动性封板池）；⑤ 资金承接拉 `get_sector_5min_kline_history` 取板块近10日5分K 写 `kline:5min:sector:{s}`。v2 路径**不**调用旧 `get_sector_5min_kline`（当日版）写当日5分K |
| `cli.py` / 入口 | 新增 `--scorers v2`（或等价开关/环境变量）选择走旧 `scorers` 还是新 `scorers_v2` 路径，默认可暂留旧路径，灰度验证后切换 |

#### Phase A / B 候选筛选改动（重点）
- **领涨板块数**：`RANK_UP_COUNT` 8 → **5**（领跌板块 `RANK_DOWN_COUNT=20` 不变，仍供资金承接用）。
- **原候选逻辑**：每个领涨板块按 5 日累计涨幅排序，仅取 **top5** 成分股进候选池。
- **新候选逻辑**：取每个领涨板块内**当日所有涨停个股**（`pct ≥ 9.9%`）进候选池，仍保留 ST/双创/北交所过滤与多概念去重。
- **理由**：龙头战法识别的对象是「当日涨停股」，从涨停榜出发评估龙头质量；top5 截断可能漏掉板块内靠后但涨停的真龙候选。领涨板块由 8 缩到 5，因每日涨停个股数本就不多，可把候选池规模控制在合理范围。
- **影响**：候选来源从「板块×5」变为「板块×涨停数」，叠加板块数收窄到5，总量大体可控；仍需关注 Phase D 封板池补拉与 pankou 的雪球请求量与 `RateLimiter` 节流。

---

## 十一、验证方式

1. **单维单测**：对每个 scorer 构造合成 K线/分时夹具（典型真龙、跟风票、一字板、补跌票、跨板块虹吸场景），断言分数区间与门槛通过/否决符合预期。
2. **聚合单测**：构造"某一大特征维低于门槛"的样本，验证一票否决生效；构造 absorption 极低但四大特征达标的样本，验证**不被否决**、仅综合分被拉低；构造全通过样本，验证加权综合分（含 absorption 10%）与排名正确。
3. **一字板专项**：一字封死票流动性维应得高分（封单大 + 开板0次），**不被惩罚、不被否决**。
4. **抗跌双基准专项**：构造「大盘跌但个股横住」「板块跌但个股翻红」两类夹具，验证大盘维度与板块维度分别加分。
5. **资金承接专项**：构造「目标板块拉升 + 其他板块同步跳水」的虹吸事件夹具，验证事件检测与三维打分；无信号时回落中性分（仅拉低综合分，**不触发否决**）。
6. **真实回放**：取一个历史题材爆发日，跑全流程，人工核对识别出的真龙是否与当日实际龙头一致。
