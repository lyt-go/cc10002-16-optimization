# 数仓分层建模与治理平台

面向原始数据落地、清洗转换、分层建模、维度事实设计、分区治理、质量校验、血缘追踪与历史补数的数据仓库工程管理平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 原始落地 | `landing` | 落地批次 | 批次编号、来源系统、落地路径 |
| 贴源层 | `ods` | 贴源表 | 表编号、表名称、来源系统 |
| 明细层 | `dwd` | 明细表 | 表编号、表名称、业务域 |
| 维度层 | `dim` | 维度表 | 维度编号、维度名称、自然键列 |
| 汇总层 | `dws` | 汇总表 | 表编号、表名称、统计粒度 |
| 应用层 | `ads` | 应用表 | 表编号、表名称、业务场景 |
| 分区治理 | `partition` | 分区方案 | 方案编号、数据表名、分区字段 |
| 质量校验 | `quality` | 质量规则 | 规则编号、校验表名、目标字段 |
| 数据血缘 | `lineage` | 血缘关系 | 关系编号、上游表名、下游表名 |
| 历史补数 | `backfill` | 补数任务 | 任务编号、目标表名、开始日期 |
| 清洗规则 | `cleanrule` | 清洗规则 | 规则编号、输入表名、空值策略 |
| 转换编排 | `transform` | 转换节点 | 节点编号、输入表名、输出表名 |
| 数据字典 | `dictionary` | 字典条目 | 条目编号、表名、字段名称 |
| 元数据管理 | `metamodel` | 元数据项 | 元数据编号、对象类型、对象名称 |
| 数据生命周期 | `lifecycle` | 生命周期策略 | 策略编号、数据表名、保留天数 |
| 存储成本 | `cost` | 成本账单 | 账单编号、数据表名、存储大小GB |
| 同步通道 | `sync` | 同步通道 | 通道编号、来源表名、目标表名 |
| 变更审计 | `auditlog` | 变更记录 | 记录编号、操作表名、变更类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 补数与成本的扫描范围联动

补数任务与成本账单不再各自看字段，统一沿「补数记录 → 扫描批次 → 范围结论 → 账单版本/明细」
贯通，口径集中在 `backend/app/services/scan_range.py`（纯函数，可独立测试）。

- **范围结论**：基于计划区间、扫描批次的成功/失败结果与覆盖策略计算仍需扫描范围。
  已成功补数不会再次估算为待处理；任务终止后水位之外未成功的计划立即标记为「已失效」。
- **并发去重**：同一天被多个任务扫描时，只承认「已成功且覆盖策略更严格」的区间
  （严格度：增量覆盖 < 分区覆盖 < 全量覆盖），已覆盖天数按天去重，但来源任务与
  最终采用区间都保留在账单明细里。
- **扫描量口径**：`plan_scan_gb` 只由计划区间决定、重试不改变；`actual_scan_gb`
  按每个扫描批次（含重试）累计；`retry_scan_gb` 只累计重试批次。
- **账单版本**：同一统计月份 + 目标表只允许一个当前版本。重复生成或扫描批次回写时
  产生新版本，旧当前版本转为历史归档、只读且明细可追溯；账单版本记录 `exec_id`、
  `plan_scan_gb`、`actual_scan_gb`、`retry_scan_gb`、`covered_date_count` 以及
  `watermark_before/after`。
- **失效与保留**：完成水位推进后，账单中未执行部分立即失效（行状态=已失效），
  历史已执行量随旧版本保留。
- **跨入口联动**：补数列表内嵌同一份范围结论，并可带 `table/month` 跳转账单页；
  账单明细支持按统计月份、目标表、覆盖策略、行状态筛选与分页，可直接定位待处理日期
  与重试扫描。

关键接口：

| 接口 | 说明 |
| --- | --- |
| `GET /api/scan/range?table=&month=` | 补数页与账单页共用的范围结论 |
| `POST /api/scan/batches` | 登记一次扫描执行（重试同入口，自动推进水位并刷新账单） |
| `GET /api/scan/batches` | 扫描批次列表，可只看重试 |
| `GET /api/cost` / `POST /api/cost` | 账单版本列表 / 生成或重新生成当前版本 |
| `GET /api/cost/history?table=&month=` | 同月同表的全部账单版本 |
| `GET /api/cost/lines` | 账单明细分页（月份/目标表/覆盖策略/行状态/仅重试/历史版本） |

回归测试（仅标准库）：

```bash
cd backend && .venv/bin/python -m unittest discover -s tests -v
```

领域：数据工程
子方向：数仓与数据湖
