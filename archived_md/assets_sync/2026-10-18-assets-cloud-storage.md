# 数据资产云端存储方案调研（阶段 1 · 修订版 v2）

> 调研日期：2026-10-18
> 修订：根据 `research-reviewer` 第 1 轮 REVISE（1 CRITICAL / 6 MAJOR / 3 MINOR）补充。
> 背景：dragon-quant 每日由 `prep` / `auction` 采集原始数据资产（约 280KB/交易日，10 年约 1GB），需要长期留存到免费云端，并支持按日期范围增量拉回本地、md5/sha256 校验。
> 范围：已排除 GitHub 私有仓库 / Git LFS / GitHub Releases / Gitee / 网盘类 / 阿里云 OSS，仅比较 6 个对象存储候选。

## 0. 结论（修订）

1. **架构结论优先于厂商结论**：Cloudflare R2 与 Backblaze B2 都提供 **S3 兼容 API**。因此方案应实现**一个 S3 兼容后端 + SigV4 签名**，把「选哪家」降级为**配置项**，而不是把某一家写死进架构。
2. **R2 是「候选主通道（待实测）」**。出口完全免费、额度 10 倍余量；但大陆可达性只有社区证据，必须先通过可执行的连通性门槛。
3. **B2 是候选备通道（待实测）**：10GB 永久免费、Class D 2500 次/日免费、出口 = 月均存储 3 倍（1GB 存储对应约 3GB/月免费出口）。
4. **七牛 Kodo 按用户约束排除**：其存储免费，但免费额度**不含外网流出流量**，S3 GET 按 0.26–0.29 元/GB 计费。虽然国内可达性最好、10 年全量成本仅约 0.3 元，但用户要求「只选出口免费的」，因此不作为主备候选（保留调研记录备查）。
5. Supabase（1GB 零余量 + 暂停）、又拍云（无长期免费）、腾讯 COS（仅 6 个月）不满足硬约束。

## 1. 对比表

| 维度 | Cloudflare R2 | Backblaze B2 | Supabase Storage | 七牛云 Kodo（已排除） | 又拍云 | 腾讯云 COS |
|---|---|---|---|---|---|---|
| 长期免费额度 | 10 GB-月存储；Class A 100 万/月；Class B 1000 万/月；出口免费 | 10 GB 存储永久免费；出口 = 月均存储 3 倍；Class A/B/C 免费，Class D 2500 次/日免费 | 1 GB 存储；出口 5 GB + 缓存 5 GB；API 不限量 | 10 GB 标准存储/月；CDN 回源 10 GB；PUT/DELETE 10 万次；GET 100 万次；上传流量不限；**不含外网流出** | 无长期免费额度；仅 121 元礼包 | 仅新用户 6 个月 50 GB |
| 长期免费 | 是，无到期日 | 是，永久 | 是（但会暂停） | 存储免费；**下载按量计费** | 否 | 否 |
| 下载/出口计费 | 免费 | 出口 = 月均存储 3 倍，超出 $0.01/GB | 出口 5 GB 免费 | **免费额度不含外网流出；0.26–0.29 元/GB**（10 年全量约 0.3 元） | — | 不含请求与流量 |
| 绑卡 | 账号已有支付方式 | 按量付费需信用卡或发票（注册是否强制未验证） | 通常无需（未验证） | 通常无需 | 无需 | 无需（充值） |
| 实名 / ICP | 不需要 | 不需要 | 不需要 | **需实名认证**；S3 端点不需要 ICP | 需实名（未验证） | 实际需实名（未验证） |
| 大陆可达性 | 无中国网络；社区反馈多数地区可通但慢、个别地区不可达；**可用 Worker + 自定义域名中转缓解** | 可访问、速度一般；无中国节点（未实测） | 走 AWS，波动（未验证） | **国内厂商，速度最好** | 国内 | 国内 |
| API 形态 | S3 兼容（`https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, region=auto） | 原生 B2 REST 或 S3 兼容 | REST | **S3 兼容（`s3.<region>.qiniucs.com`，SigV4）** + 原生 API | REST | S3 兼容 + JSON API |
| 标准库可行性 | SigV4 可手写 | 原生 API 更简单，或 SigV4 | urllib 即可 | SigV4 可手写 | 可手写 | SigV4 可手写 |
| 单文件 / 限制 | PUT 5 GiB；同 key 写 1 次/秒；key 1024 B；元数据 8192 B | 未验证 | 50 MB；1 周不活动暂停 | 未验证；S3 端点必须签名、不支持匿名 | — | 未验证 |
| 版本控制 | **不支持** | 支持（需开启） | 无 | 无 | — | 支持（需开启） |
| 主要风险 | 大陆速度/地区可达性 | 出口额度受 3× 存储绑定 | 容量零余量、暂停 | 实名；S3 协议不得匿名访问 | 无长期免费 | 6 个月后计费 |

### 容量与请求量适配（不变）

10 年约 1GB：R2 / B2 / 七牛均为 10GB 额度（10% 占用）；请求约 500 次/年，任何免费额度都可忽略。**容量和请求量不是选型依据。**

## 2. 各候选要点（修订）

### Cloudflare R2
- 免费档：10 GB-月 Standard、Class A 100 万/月、Class B 1000 万/月、出口全免费，无到期日（<https://developers.cloudflare.com/r2/pricing/>）。
- 限制：单次 PUT 5 GiB、分片 4.995 TiB、1 万片、同 key 写 1 次/秒（<https://developers.cloudflare.com/r2/platform/limits/>）。
- S3 兼容子集；官方兼容表显示 `PutObject` 支持 `Content-MD5`，但版本控制 API 未实现（<https://developers.cloudflare.com/r2/api/s3/api/>）。
- **大陆可达性缓解路径（新增）**：
  1. 直连 S3 端点 `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`（最简单，但不可走优选 IP）。
  2. **Worker + R2 binding + 自定义域名中转**：客户端仍用普通 HTTPS，Worker 校验共享 Token 后调用 `env.BUCKET.put/get`。自定义域名必须已托管在同一 Cloudflare 账号（<https://developers.cloudflare.com/r2/buckets/public-buckets/>）。
  3. Worker 免费档：10 万请求/日、10ms CPU、100MB 请求体、50 子请求/次、128MB 内存、6 个并发上游连接（<https://developers.cloudflare.com/workers/platform/limits/>）。本场景 280KB/日，绰绰有余。
  - 注意：Worker 中转仍经过 Cloudflare 边缘，**不保证**改善大陆可达性，必须实测对比。
- 无版本控制 → 用内容哈希或日期命名 key、永不覆盖。

### Backblaze B2
- 10 GB 永久免费；出口 = 月均存储 3 倍，超出 $0.01/GB；Class A/B/C 免费，**Class D 前 2500 次/日免费**，之后 $0.004/万次（<https://www.backblaze.com/cloud-storage/pricing>）。
- 出口配额对增量拉取充足；一次性回灌 1GB 历史会接近 3GB/月上限。
- 原生 REST 用 Bearer token，签名比 SigV4 简单；也提供 S3 兼容层。
- 大陆无中国节点，速度一般（未实测）。

### Supabase Storage
- 1 GB 存储、单文件 50 MB、出口 5 GB + 缓存 5 GB；免费项目 1 周不活动暂停、最多 2 个活跃项目（<https://supabase.com/pricing>）。
- 1 GB 恰好等于 10 年总量，**零余量**；不推荐。

### 七牛云 Kodo（已排除：下载计费）
- 免费：标准存储 10 GB/月、CDN 回源 10 GB、PUT/DELETE 10 万次、GET 100 万次、上传流量不限；面向实名认证用户，未写到期日（<https://www.qiniu.com/prices/kodo>）。
- **免费额度不含外网流出流量**：华东-浙江2 为 0.26 元/GB（0–100TB），华南-广东/华东-浙江/华北-河北为 0.29 元/GB。价格页未把「S3 端点 GET」显式映射到「外网流出」计费项，**该映射待确认**；按最坏情况估算，10 年全量回灌 1GB 成本约 0.3 元，日常增量拉取可忽略。
- **S3 兼容端点（本次核实）**：`s3.<region-id>.qiniucs.com`，如华东 `s3.cn-east-1.qiniucs.com`；支持 path-style 与 virtual-host style；**必须携带 AWS Signature，不支持匿名访问**；**官方签名文档明确同时兼容 AWS Signature V2 与 V4**（<https://developer.qiniu.com/kodo/4088/s3-access-domainname>、<https://developer.qiniu.com/kodo/4093/s3-authentication>）；S3 bucket 名需全局唯一。
- **此前「测试域名 30 天 / 自定义域名需 ICP」的风险，仅影响 CDN 域名访问；走 S3 端点读写不需要绑定 CDN 域名**。该风险对 S3 方案不成立。
  - 仍注意：若未来需要浏览器直链下载/静态托管，才涉及 CDN 域名与备案。
- 剩余门槛：**实名认证**。
- **结论：按用户「只选出口免费」的约束排除**；国内可达性优势与 S3 端点事实保留备查，不再参与主备选择。

### 又拍云 / 腾讯云 COS
- 又拍云无长期免费额度（仅 121 元礼包）→ 出局。
- 腾讯 COS 免费仅新用户 6 个月 50GB，不含请求与流量 → 出局。

### 范围外候选的排除理由（新增）
- Oracle Cloud Always Free Object Storage：需要国际信用卡与海外账号，大陆注册/可达性不确定。
- Storj：免费额度小且依赖加密分片网络，国内可达性未验证。
- 自建 NAS / 对象存储：不满足「云端异地备份」目标。

## 3. 推荐（修订）

### 推荐组合：S3 兼容可插拔后端 + 实测决定主备

```
R2 (S3 endpoint)      ─┐
                       ┼─→ 同一 S3Backend（SigV4）→ 同一 manifest/同步管线
B2 (S3 endpoint)      ─┘
```

- **代码层面只实现一次 SigV4 + S3 操作**（PUT/GET/HEAD/LIST/DELETE），通过 `endpoint / region / bucket / credentials` 配置切换。
- **主通道由实测决定，不由调研决定**：
  - 若 R2 通过连通性门槛 → R2 主、B2 备。
  - 若 R2 不通过、B2 通过 → B2 主、R2 备（接受较慢速度与出口三倍配额）。
  - **若两家都不通过 → 这是阻塞项，必须停下向用户重新裁决**，不能在方案里假装存在兜底。可选方向只有两个：
    - 放宽「出口免费」约束，接受七牛按量下载费（需用户重新授权）；
    - 接受慢速链路，并满足量化口径：P95 上传 ≤ 30s、日成功率 ≥ 99%、失败对象在本地排队不丢、单次重试上限 5 次且指数退避。达不到该口径即视为不可用。
- 这样即使厂商判断变化，也不需要返工方案主体。

### 决策门槛（可执行，新增）

**门槛 A：冒烟（立即执行，1 小时内）**
- 从本机与手机热点（至少两条出口线路）各上传 50 次、下载 30 次 280KB 对象。
- **对 R2（S3 直连 + Worker 中转）与 B2 使用同一个 SigV4 客户端，分别完成 PUT / GET / HEAD / LIST 四项操作**；HEAD 返回的 `x-amz-meta-sha256` 与 `Content-Length` 必须与本地一致。
- 通过判据：上传成功率 ≥ 99%；P95 单次上传 < 10s；下载内容 sha256 全部一致；无连续 2 次失败；两家、两条链路的四项操作全部成功且 HEAD 元数据一致。
- 记录每次操作的成功/失败、耗时与错误码，作为主备裁决依据。

**门槛 B：7 天 soak（决定主/备）**
- 连续 7 个自然日，每日自动上传并回读校验。
- 通过判据：7 天累计成功率 ≥ 99%；任一日失败后次日自动补传成功；P95 上传 < 10s。
- 未通过 → 按上文规则切换主通道。

**在门槛 A/B 通过前，文档与输出中一律写「候选主通道（待实测）」，不写「首选」。**

## 4. launchd 与跨平台调度边界（修订）

- **同步核心 = 平台无关的纯 Python CLI**（`python -m dragon_quant assets push|pull|status|verify`）。
- **调度层只是触发器**：
  - macOS：`launchd`（`StartCalendarInterval` + `RunAtLoad`）。
  - Windows：任务计划程序（`schtasks`），语义等价。
  - Linux：cron/systemd timer。
- **补跑不依赖调度器**：启动时读本地 `state.json`（`last_success_trade_date`），并与远端 manifest 对比；只要存在本地已有、云端缺失的资产就补传。因此睡眠、关机、断网、换平台都能自愈。
- launchd 行为（本机 `man 5 launchd.plist`）：睡眠期间错过 → 唤醒后**合并补跑 1 次**；关机错过不承诺补跑（推断）。这正是需要 `state.json` 幂等补跑的原因。

## 5. 数据形态与导出边界（新增，回应 MAJOR）

### 上传单元定义

| 来源 | 表 | 上传单元 | 说明 |
|---|---|---|---|
| prep 原始资产 | `prep.db / prep_assets` | `(trade_date, asset_type)` → 一个对象 | 5 类资产 + `collection_report` 各自独立 |
| 集合竞价 | `dragon.db / auction_snapshots` | `trade_date` 聚合 → 一个对象 | 按 code 排序，避免单股一个对象造成碎片 |
| 算法快照 | `prep.db / prep_snapshots` | **不上传** | 可重算，不属于资产 |

- 对象命名建议：`assets/<trade_date>/<asset_type>.json.gz`、`assets/<trade_date>/auction_snapshots.json.gz`。
- `collection_report` 一并上传，用于解释当天采集是否降级。
- **不可再生资产清单**（必须备份）：`market_breadth`、`fg_boards`、`fund_flow`（same_day_only）；其余资产也应备份，但可由数据源重采。

### SQLite 一致性导出

- **不要直接复制运行中的 `.db` 文件**（WAL 下会得到不一致快照）。
- 逻辑导出：单连接开启只读事务（`BEGIN` → 读取 → `COMMIT`），保证读到的是一致快照；逐行 `json.loads` 后重新序列化（`sort_keys=True`, `allow_nan=False`）以得到确定性字节。
- 若未来需要整库归档：使用 `sqlite3.Connection.backup()` 或 `VACUUM INTO`，而不是文件拷贝。
- `auction_snapshots` 位于 `dragon.db`，**必须纳入备份范围**。其不可再生的真实原因是 `get_auction` 只返回当日数据、无历史回看（`storage clear --all` 只清理 cache/results/logs/shared，不删除任何 `.db`）。

### pull 方向语义（新增，回应 MAJOR）

- **默认 dry-run**：`assets pull` 不带 `--apply` 时只输出「将下载 N 个分片 / 本地缺失 M 个 / 哈希冲突 K 个」，不写盘。
- **恢复流程**：下载到 **`<DQ_DATA_DIR>/tmp/`（必须与目标文件同卷）** → 全量校验 sha256 与 size → 校验通过后用 `os.replace` 原子替换；任一步失败保留现场、不产生半写入。**禁止**用系统临时目录或任何可能跨卷的位置做暂存（`os.replace` 跨卷会抛 `EXDEV`）。
- **本地权威**：本地已存在且哈希不同的资产**默认不覆盖**；确需用云端版本恢复时显式 `--force`，并在输出中列出被替换的 `(date, asset_type, 本地 sha256 → 云端 sha256)`。
- **冲突裁决**：manifest 冲突以**本地为准**；云端 manifest 只用于发现「本地缺失的对象」，不作为覆盖依据。
- **范围参数**：`--from/--to` 按 trade_date 过滤；`--missing-only`（默认）只拉本地没有的资产。

## 6. 凭据管理（新增，回应 MAJOR）

- **读取顺序**：环境变量 → `DQ_DATA_DIR/secrets/assets_sync.json`（POSIX 0600）→ 报错。
- **禁止**：写入 manifest、日志、CLI 输出、git 仓库。
- **最小权限**：
  - R2：API Token 限定单桶 Object Read & Write。
  - 七牛：使用子账号/最小权限，AK/SK 只授予目标空间。
  - B2：Application Key 限定单桶。
- **轮换与吊销**：文档记录轮换步骤；泄露时立即吊销并在本地删除配置。
- **日志脱敏**：错误输出只打印 endpoint/bucket/key，不打印签名头与密钥。

## 7. manifest 的云端副本与恢复（新增，回应 MAJOR）

- **manifest 也上传云端**：`manifests/<trade_date>.json`，并在对象中记录 `schema` 版本。
- **manifest 丢失可重建**：先用 `ListObjectsV2` 按 `assets/<date>/` 前缀枚举 key 与 size（该 API 不返回用户元数据），再**逐对象 HEAD** 读取 `x-amz-meta-sha256` 与 `Content-Length`，据此重建日 manifest。对象量级 ≤10 个/交易日，HEAD 成本可忽略。注意：各厂商 ETag 语义不一致（原生 API 与 S3 兼容层、单段与分片上传均可能不同），**一律不能把 ETag 当内容哈希**。
- **写入顺序**：先传资产对象 → HEAD 校验 size + 自定义元数据 sha256 → 再写该日 manifest。manifest 是提交点。
- **恢复演练**：每季度抽样 30 天，从云端拉取并逐片 sha256 校验；结果记录到文档。
- 云端无版本控制（R2/七牛）→ key 不用覆盖写；同一 `(date, asset_type)` 内容变化时使用内容哈希后缀，旧版本保留。

## 8. 未验证假设清单（修订）

1. R2 大陆可达性：仅社区反馈，未在本机实测；Worker 中转效果同样未实测。
2. B2 注册是否强制绑卡、大陆实测速度：未核实。
3. Supabase 免费档是否需绑卡、大陆实测可达性：未核实。
4. 七牛测试域名有效期/备案要求：仅第三方资料，官方页未核实；**对 S3 端点方案不构成阻塞**。
5. 腾讯云 COS 是否强制实名：官方免费额度文档未明写。
6. launchd 关机期间错过是否补跑：man page 未明确，属推断。
7. 七牛/腾讯/B2 的单文件大小上限：未逐一核实，280KB 场景不构成约束。
8. **七牛 S3 端点 GET 是否按「外网流出流量」计费**：价格页未做端点→计费项映射，按最坏情况估算成本约 0.3 元/10 年，不改变结论。
9. 七牛 S3 签名：官方文档明确兼容 V2/V4（已核实），但具体 region/service 取值未在文档示例中给出，需在门槛 A 的互操作测试中确认。

已核实并修正：
- R2 `PutObject` **支持** `Content-MD5`（官方兼容表）。
- B2 **Class D 2500 次/日免费**（官方定价页）。

## 9. 来源

- Cloudflare R2 定价：<https://developers.cloudflare.com/r2/pricing/>
- Cloudflare R2 限制：<https://developers.cloudflare.com/r2/platform/limits/>
- Cloudflare R2 S3 API：<https://developers.cloudflare.com/r2/api/s3/api/>
- Cloudflare R2 公共访问/自定义域名：<https://developers.cloudflare.com/r2/buckets/public-buckets/>
- Cloudflare Workers 限制：<https://developers.cloudflare.com/workers/platform/limits/>
- Backblaze B2 定价：<https://www.backblaze.com/cloud-storage/pricing>
- Supabase 定价：<https://supabase.com/pricing>
- 七牛云 Kodo 价格：<https://www.qiniu.com/prices/kodo>
- 七牛云 Kodo S3 兼容端点：<https://developer.qiniu.com/kodo/4088/s3-access-domainname>
- 七牛云 Kodo S3 签名认证（兼容 V2/V4）：<https://developer.qiniu.com/kodo/4093/s3-authentication>
- 又拍云定价：<https://www.upyun.com/pricing>
- 腾讯云 COS 免费额度：<https://cloud.tencent.com/document/product/436/6240>
- R2 大陆体验讨论（社区，非官方）：<https://www.v2ex.com/t/1110783>
- launchd.plist 手册：本机 `man 5 launchd.plist`（Apple 官方）
