# 🐉 dragon-quant

龙头战法量化分析系统 — 从当日涨停股中，通过四维量化评分识别真龙头。

纯 Python 3 标准库，**零依赖**，下载即用。

---

## 快速开始

```bash
# 每日扫榜（推荐）
python -m dragon_quant                    # 输出 Top 5
python -m dragon_quant --top 10           # 输出 Top 10
python -m dragon_quant --workers 3        # 3 并发分析

# 单票深挖
python -m dragon_quant.analyze 002xxx     # 输入股票代码
python -m dragon_quant.analyze 002xxx -v  # 详细报告
```

## 四维评分体系

| 维度 | 权重 | 核心问题 |
|------|------|---------|
| 🐉 带动性 | 35% | 封板后小弟跟不跟？跟多紧？ |
| 🛡️ 抗跌性 | 15% | 大盘跳水时扛不扛得住？ |
| 📊 领涨性 | 25% | 平时在同行业排第几？ |
| 💰 资金承接 | 25% | 别板块跳水时资金是否涌入并持续到收盘？ |

## 评级

| 评级 | 分数 | 含义 |
|------|------|------|
| 🐉 真龙 | 85-100 | 四维共振，引领板块 |
| ⭐ 强票 | 70-84 | 某方面突出，可持续跟踪 |
| 📊 中规中矩 | 50-69 | 还行但缺少亮点 |
| 🐔 杂毛 | < 50 | 跟风货，远离 |

## 数据来源

全部来自东方财富、腾讯、雪球、新浪财经的公开接口，无需注册或登录。

---

## 作为 package 安装

```bash
pip install -e .
# 然后可以直接用命令
dragon-quant
dragon-quant --top 10
dq-analyze 002xxx
```
