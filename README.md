# 栖木印刷配对试验簿

同纸张两工艺成对色差→配对分析→试印复核；枚举n<=18，以上固定seed Monte Carlo加一校正。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
栖木研究协作组，12名分析师与业务人员，日常使用 `paired-press-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：PressStudy / MatchedPair / PairedRun / Review；编号读数与配对预览为 ProcessReading / PairingPreview / ActivePairing。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 试印研究、成对纸样录入/编辑/删除；重名记录与非法数值明确拒绝。
- 两道工艺各自上传带编号的色差读数与测量时刻（可单条或整批 CSV；编号不必对应）。时差上限内的候选做一对一配对：先使配对数最多，同数时总时差最小。预览只确认所有最优方案都使用的配对，并列出未配读数及原因（无候选 / 未进入任何最优方案 / 存在并列最优）。
- 确认时校验读数指纹；期间读数被并发修改则该预览作废、原配对整批保留，需重新预览。确认成功则在一次事务内切换本批分析使用的配对；已冻结的运行仍按自己的快照可查。
- 配对差均值与精确符号翻转置换检验。同一纸张上成对测量；小样本枚举全部符号，较大样本使用固定种子 Monte Carlo 与加一校正。
- 每次运行冻结参数、观测和SHA256。Redis任务由独立worker计算，写DB与共享产物卷；输入修改不会改写历史结果。
- 负责人复核不可覆盖；审计记录登录、数据改动、预览/确认、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18242 ，API http://localhost:19242/docs 。数据库映射 17742。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

## 一天的工作剧本
1. 09:00 admin登录，查“栖木·米白纸套印”。
2. 09:10 analyst核对 `paired-press-202611-1.csv` 的seed记录。
3. 09:15 读数配对页检查原工艺/新工艺两份编号读数与测量时刻；演示批量已有一份已确认预览，可另设时差上限重新预览，阅读未配读数及原因。
4. 09:20 分析执行页保留默认参数 `{"resamples": 10000}`，命名本轮研究。
5. 09:21 冻结输入并排队（快照为本批已确认的共有配对），worker实算，结果页自动刷新。
6. 09:25 阅读核心数值、方法边界、明细与SHA256；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2、SciPy1.15，固定seed42|
|数据库|PostgreSQL16|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本（v2 起步进式加列，不改写数据）。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 算法输入、失败态与复现
输入为选定批次已确认的配对（或未启用读数功能时的旧成对记录）和参数，输出核心数值、明细、解释；参数经后端严格白名单和范围验证。同一纸张上成对测量；小样本枚举全部符号，较大样本使用固定种子 Monte Carlo 与加一校正。空批次拒绝；不完整字段/非有限数/非法计数拒绝；常量相关列与不足样本使任务failed并保留错误。固定seed42、输入checksum相同可复现。独立oracle：小n穷举手算；交换两列统计量变号p相同；缺失配对拒绝。

配对求解另以穷举 oracle 校验（`GET` 预览结果的最大配对数、最小总时差、共有配对集合须与穷举所有一对一配对一致）：`python3 -m pytest tests/test_pairing.py`。已知边界：时差以 UTC 毫秒/微秒整数计算；候选边上限 30000，超过时提示缩小时差上限；若最新读数改动使已确认配对过期，新分析被拒绝而非静默使用旧值。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务。以当前测试目录TESTING.md记录实际验证结果。

MIT License。
