# 配置系统

项目采用 `pydantic-settings`，默认从 `.env` 读取配置，支持嵌套键。

## 使用方式

1. 复制模板：
   - `Copy-Item .env.example .env`
2. 按需修改 `.env`
3. 启动服务

## 规则词表（话题 / 脱敏 / 情绪关键词）

- 离线确定性分类与事实提示词表位于 `prompt_assets/taxonomy/rule_lexicons.json`。
- 运行时代码通过 `app/core/rule_lexicons.py` 读取（含缺失文件时的最小内嵌回退）。
- 修改话题关键词时请保持与提示词 `prompt/ai_topic_*.md` 中的标签集合一致，或同步更新该提示词。

## 关键配置

- `APP__NAME`：应用名
- `APP__ENV`：环境名（dev/prod）
- `APP__DEBUG`：是否 debug
- `APP__TIMEZONE`：业务时间时区（默认 `Asia/Shanghai`）

- `STORAGE__SQLITE_DB_PATH`：SQLite 文件路径
- `STORAGE__POSTGRES_DSN`：预留 PostgreSQL DSN（当前默认未启用）
- `STORAGE__VECTOR_DSN`：预留向量服务地址（当前默认未启用）
- `STORAGE__VECTOR_COLLECTION`：预留向量集合名

- `EMOTION__DECAY`：全局情绪衰减系数
- `EMOTION__GAIN`：全局情绪增益系数
- `EMOTION__MAX_HISTORY`：情绪历史窗口

- `RETRIEVAL__TOP_K`：向量召回数量
- `RETRIEVAL__MAX_ROUNDS`：单次请求最大检索轮次
- `RETRIEVAL__MIN_SCORE`：最小召回分数
- `RETRIEVAL__HEAT_BOOST_WEIGHT`：热度加权系数
- `RETRIEVAL__HEAT_DECAY_PER_DAY`：热度按天衰减
- `RETRIEVAL__HEAT_INCREMENT_ON_ACCESS`：检索命中后的热度增量
- `RETRIEVAL__RECENCY_WINDOW_DAYS`：仅检索最近 N 天向量记忆
- `RETRIEVAL__CROSS_POSITIVE_THRESHOLD`：正向关系下跨对话召回阈值
- `RETRIEVAL__CROSS_NEUTRAL_THRESHOLD`：中性关系下跨对话召回阈值
- `RETRIEVAL__CROSS_NEGATIVE_THRESHOLD`：负向/拒绝关系下跨对话阈值（通常更严格）
- `RETRIEVAL__RELATION_ACCESS_MIN_STRENGTH`：允许跨对话检索的最小关系强度

- `PERSONA__NAME`：拟人人设名
- `PERSONA__POLICY_NOTICE_ON_FIRST_TURN`：首轮是否提示非私密声明
- `PERSONA__ASSETS_DIR`：人设/提示词 Markdown 资源目录（默认 `prompt_assets`）

- `SESSION__RENEW_AFTER_HOURS`：会话续期阈值（小时）

- `PROFILE__RECENT_MESSAGE_LIMIT`：画像生成时读取的最近消息条数

- `JOBS__ENABLED`：是否启用异步任务队列
- `JOBS__WORKER_COUNT`：任务 worker 数量
- `JOBS__QUEUE_SIZE`：任务队列容量
- `JOBS__MAX_RETRIES`：任务最大重试次数
- `JOBS__DEAD_LETTER_LIMIT`：死信队列上限
- `JOBS__MAINTENANCE_ENABLED`：是否启用定时维护任务
- `JOBS__MAINTENANCE_INTERVAL_SECONDS`：维护任务执行间隔
- `JOBS__EMOTION_WINDOW_MINUTES`：情绪聚合窗口（分钟）

- `LLM__PROVIDER`：当前建议固定 `kilo`
- `LLM__MODEL`：模型名（例如免费模型名）
- `LLM__API_KEY`：Kilo 网关密钥
- `LLM__BASE_URL`：Kilo 网关地址（默认 `https://api.kilo.ai/api/gateway`）
- `LLM__TEMPERATURE`：生成温度
- `LLM__TIMEOUT_SECONDS`：模型请求超时秒数
- `LLM__STARTUP_HEALTHCHECK_ENABLED`：启动时是否执行 LLM 健康检查（失败仅记录日志，不阻断启动）
- `LLM__RETRY_MAX_ATTEMPTS`：单次请求最大尝试次数（含首次）
- `LLM__RETRY_BACKOFF_SECONDS`：重试退避基数（秒，按尝试次数线性增长）
- `LLM__CIRCUIT_BREAKER_FAILURE_THRESHOLD`：连续失败达到阈值后开启熔断
- `LLM__CIRCUIT_BREAKER_OPEN_SECONDS`：熔断开启时长（秒）

- `RHYTHM__ENABLED`：是否启用节奏控制
- `RHYTHM__SILENCE_SECONDS`：输入静默判定阈值
- `RHYTHM__ENABLE_MAX_THINK_SECONDS`：是否启用单轮 `MAX_THINK` 截断；为 `false` 时批处理线程会一直等到模型调用结束，不再返回“思考超时”占位文案（若整体仍超过 `RHYTHM__WAIT_TIMEOUT_SECONDS`，外层等待会抛超时）
- `RHYTHM__MAX_THINK_SECONDS`：启用 `ENABLE_MAX_THINK` 时，单轮批处理执行的上限（秒）
- `RHYTHM__COOLDOWN_SECONDS`：批次处理冷却时间
- `RHYTHM__SINGLE_MESSAGE_CHAR_THRESHOLD`：单条消息字符阈值
- `RHYTHM__SINGLE_MESSAGE_TOKEN_THRESHOLD`：单条消息 token 阈值
- `RHYTHM__WINDOW_CHAR_THRESHOLD`：窗口总字符阈值
- `RHYTHM__WINDOW_TOKEN_THRESHOLD`：窗口总 token 阈值
- `RHYTHM__ENABLE_TERMINATE_KEYWORDS`：是否启用中止关键词
- `RHYTHM__TERMINATE_KEYWORDS`：中止关键词列表
- `RHYTHM__WAIT_TIMEOUT_SECONDS`：窗口等待超时

说明：`kilo` provider 使用 OpenAI 官方 Python SDK，以 OpenAI 兼容模式连接 Kilo 网关；当网关不可用时，系统将统一返回“当前不可用，请联系管理员（debug账号）”，不再回退 mock 回复。

## 参数约束化（已启用）

- 行为参数会在运行时被统一转换为“当前值 + 值域 + 分段含义 + 示例对照”，并注入系统提示词。
- 语义规格来源：`app/core/config.py` 中 `build_behavior_parameter_specs()`。
- 参数文案与示例来源：`prompt_assets/param_controls/behavior_specs.json`（避免硬编码在 Python 中）。
- 提示词注入位置：
  - `prompt_assets/param_controls/behavior_control.md`
  - `prompt_assets/prompt/system_wrapper.md` 的 `参数控制` 区块
- 冲突优先级：安全与隐私边界 > 终止/节奏 > 关系与检索 > 情绪与风格。

### 纳入约束的参数集合

- `EMOTION__DECAY` / `EMOTION__GAIN` / `EMOTION__MAX_HISTORY`
- `RETRIEVAL__TOP_K` / `RETRIEVAL__MAX_ROUNDS` / `RETRIEVAL__MIN_SCORE`
- `RETRIEVAL__HEAT_BOOST_WEIGHT` / `RETRIEVAL__HEAT_DECAY_PER_DAY` / `RETRIEVAL__HEAT_INCREMENT_ON_ACCESS`
- `RETRIEVAL__RECENCY_WINDOW_DAYS` / `RETRIEVAL__CROSS_POSITIVE_THRESHOLD` / `RETRIEVAL__CROSS_NEUTRAL_THRESHOLD`
- `RETRIEVAL__CROSS_NEGATIVE_THRESHOLD` / `RETRIEVAL__RELATION_ACCESS_MIN_STRENGTH`
- `PERSONA__NAME` / `PERSONA__POLICY_NOTICE_ON_FIRST_TURN` / `PERSONA__ASSETS_DIR`
- `SESSION__RENEW_AFTER_HOURS`
- `PROFILE__RECENT_MESSAGE_LIMIT`
- `LLM__PROVIDER` / `LLM__MODEL` / `LLM__API_KEY` / `LLM__BASE_URL` / `LLM__TEMPERATURE`
- `LLM__TIMEOUT_SECONDS` / `LLM__STARTUP_HEALTHCHECK_ENABLED` / `LLM__RETRY_MAX_ATTEMPTS`
- `LLM__RETRY_BACKOFF_SECONDS` / `LLM__CIRCUIT_BREAKER_FAILURE_THRESHOLD` / `LLM__CIRCUIT_BREAKER_OPEN_SECONDS`
- `RHYTHM__ENABLED` / `RHYTHM__SILENCE_SECONDS` / `RHYTHM__ENABLE_MAX_THINK_SECONDS` / `RHYTHM__MAX_THINK_SECONDS`
- `RHYTHM__COOLDOWN_SECONDS` / `RHYTHM__SINGLE_MESSAGE_CHAR_THRESHOLD` / `RHYTHM__SINGLE_MESSAGE_TOKEN_THRESHOLD`
- `RHYTHM__WINDOW_CHAR_THRESHOLD` / `RHYTHM__WINDOW_TOKEN_THRESHOLD` / `RHYTHM__ENABLE_TERMINATE_KEYWORDS`
- `RHYTHM__TERMINATE_KEYWORDS` / `RHYTHM__WAIT_TIMEOUT_SECONDS`

### 示例约束规则

- 同一用户输入在不同参数分段下会注入不同示例，模型必须按当前分段输出。
- 当 `LLM__API_KEY` 为空时，提示词会显式声明降级状态，禁止伪装“完整模型链路”。
- `PERSONA__POLICY_NOTICE_ON_FIRST_TURN=true` 时首轮会注入策略提示；`false` 时不注入。

## OneBot 11 配置

- `ONEBOT__ENABLED`：是否启用 OneBot WS 客户端
- `ONEBOT__WS_URL`：OneBot WS 地址（支持填 `http/https`，启动时会自动转成 `ws/wss`）
- `ONEBOT__TOKEN`：OneBot 鉴权 token
- `ONEBOT__MESSAGE_FORMAT`：消息格式（当前按 `array` 处理）
- `ONEBOT__RECONNECT_INTERVAL_SECONDS`：断线重连间隔
- `ONEBOT__DEBUG_ONLY_USER_ID`：debug 模式仅处理的 QQ 用户 ID
- `ONEBOT__FORCE_GROUP_WHITELIST`：是否启用群聊强制白名单（开启后仅处理白名单群）
- `ONEBOT__GROUP_AUTONOMOUS_WHITELIST`：群白名单（JSON 数组，群号列表）
