# 配置系统

项目采用 `pydantic-settings`，默认从 `.env` 读取配置，支持嵌套键。

## 使用方式

1. 复制模板：
   - `Copy-Item .env.example .env`
2. 按需修改 `.env`
3. 启动服务

## 关键配置

- `APP__NAME`：应用名
- `APP__ENV`：环境名（dev/prod）
- `APP__DEBUG`：是否 debug
- `APP__TIMEZONE`：业务时间时区（默认 `Asia/Shanghai`）

- `STORAGE__SQLITE_DB_PATH`：SQLite 文件路径

- `EMOTION__DECAY`：全局情绪衰减系数
- `EMOTION__GAIN`：全局情绪增益系数
- `EMOTION__MAX_HISTORY`：情绪历史窗口

- `RETRIEVAL__TOP_K`：向量召回数量
- `RETRIEVAL__MIN_SCORE`：最小召回分数
- `RETRIEVAL__RECENCY_WINDOW_DAYS`：仅检索最近 N 天向量记忆
- `RETRIEVAL__CROSS_POSITIVE_THRESHOLD`：正向关系下跨对话召回阈值
- `RETRIEVAL__CROSS_NEUTRAL_THRESHOLD`：中性关系下跨对话召回阈值
- `RETRIEVAL__CROSS_NEGATIVE_THRESHOLD`：负向/拒绝关系下跨对话阈值（通常更严格）
- `RETRIEVAL__RELATION_ACCESS_MIN_STRENGTH`：允许跨对话检索的最小关系强度

- `PERSONA__NAME`：拟人人设名
- `PERSONA__POLICY_NOTICE_ON_FIRST_TURN`：首轮是否提示非私密声明
- `PERSONA__ASSETS_DIR`：人设/提示词 Markdown 资源目录（默认 `prompt_assets`）

- `LLM__PROVIDER`：当前建议固定 `kilo`
- `LLM__MODEL`：模型名（例如免费模型名）
- `LLM__API_KEY`：Kilo 网关密钥
- `LLM__BASE_URL`：Kilo 网关地址（默认 `https://api.kilo.ai/api/gateway`）
- `LLM__TIMEOUT_SECONDS`：模型请求超时秒数
- `LLM__STARTUP_HEALTHCHECK_ENABLED`：启动时是否执行 LLM 健康检查（失败仅记录日志，不阻断启动）
- `LLM__RETRY_MAX_ATTEMPTS`：单次请求最大尝试次数（含首次）
- `LLM__RETRY_BACKOFF_SECONDS`：重试退避基数（秒，按尝试次数线性增长）
- `LLM__CIRCUIT_BREAKER_FAILURE_THRESHOLD`：连续失败达到阈值后开启熔断
- `LLM__CIRCUIT_BREAKER_OPEN_SECONDS`：熔断开启时长（秒）

说明：`kilo` provider 使用 OpenAI 官方 Python SDK，以 OpenAI 兼容模式连接 Kilo 网关；当网关不可用时，系统将统一返回“当前不可用，请联系管理员（debug账号）”，不再回退 mock 回复。

## OneBot 11 配置

- `ONEBOT__ENABLED`：是否启用 OneBot WS 客户端
- `ONEBOT__WS_URL`：OneBot WS 地址（支持填 `http/https`，启动时会自动转成 `ws/wss`）
- `ONEBOT__TOKEN`：OneBot 鉴权 token
- `ONEBOT__MESSAGE_FORMAT`：消息格式（当前按 `array` 处理）
- `ONEBOT__RECONNECT_INTERVAL_SECONDS`：断线重连间隔
- `ONEBOT__DEBUG_ONLY_USER_ID`：debug 模式仅处理的 QQ 用户 ID
