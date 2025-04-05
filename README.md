# MonadDEX套利机器人

## 项目简介

MonadDEX套利机器人是一个自动化工具，专为Monad区块链上的DEX（去中心化交易所）套利设计。该机器人能够实时监控多个DEX上的代币价格，自动发现并执行套利机会，帮助用户在不同交易所之间的价格差异中获利。

## 主要功能

### 1. 跨DEX套利
- 实时监控多个DEX上的代币价格差异
- 当价格差异超过设定阈值时，自动执行套利交易
- 考虑gas费用和滑点，确保套利交易有利可图

### 2. 刷交易量模式
- 支持在指定DEX上增加交易量
- 可配置交易金额范围、亏损容忍度和交易间隔
- 跟踪已累计的交易量，达到设定值后自动停止

### 3. 智能交易管理
- 自动计算最佳交易路径和金额
- 内置滑点保护，确保交易安全执行
- 支持自定义gas价格和交易参数

### 4. 交易记录与统计
- 详细记录每笔交易的执行情况
- 提供交易历史和利润统计
- 自动保存交易数据，方便后续分析

## 系统要求

- Python 3.8+
- Windows/Mac/Linux操作系统
- 互联网连接
- Monad钱包和一定数量的MON代币

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/yourusername/monadDEX.git
cd monadDEX
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv .venv
```

Windows激活虚拟环境：
```bash
.venv\Scripts\activate
```

Linux/Mac激活虚拟环境：
```bash
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

## 配置说明

### 1. 环境变量配置

复制`.env.example`文件为`.env`，然后编辑以下关键参数：

```
# 区块链连接配置
RPC_URL=https://monad-testnet.g.alchemy.com/v2/YOUR_API_KEY
CHAIN_ID=10143

# 钱包配置
PRIVATE_KEY=YOUR_WALLET_PRIVATE_KEY

# 套利配置
MAX_GAS_PRICE=52
MAX_SLIPPAGE=0.5
MIN_PROFIT_THRESHOLD=0.05
MAX_TRADE_AMOUNT=10
```

**重要提示**：请务必妥善保管您的私钥，不要泄露给任何人。

### 2. 交易所与代币配置

在`config.py`文件中，您可以配置：

- VOLUME_BOOSTING = "enabled": True,        # 重点：是否启用刷交易量模式：True启用，False禁用
- 支持的DEX及其路由器地址
- 交易代币对
- 套利参数（最小利润阈值、最大交易金额等）
- 刷交易量模式参数

## 使用方法

### Windows用户

直接运行`start_bot.bat`文件：

```bash
start_bot.bat
```

### 其他操作系统用户

```bash
python arbitrage_bot.py
```

## 工作原理

1. **价格监控**：机器人通过`price_monitor.py`持续监控各DEX上的代币价格
2. **套利识别**：当发现价格差异超过设定阈值时，计算潜在利润
3. **交易执行**：通过`transaction_executor.py`执行套利交易
4. **结果记录**：交易结果被记录在`data/trade_history.json`文件中

## 文件结构

```
├── arbitrage_bot.py      # 主套利逻辑
├── price_monitor.py      # 价格监控模块
├── transaction_executor.py # 交易执行模块
├── config.py             # 配置文件
├── .env                  # 环境变量配置
├── contracts/            # 合约ABI文件
│   ├── erc20_abi.json    # ERC20代币ABI
│   ├── router_abi.json   # 路由器ABI
│   └── ...               # 其他ABI文件
├── utils/                # 工具函数
│   └── helpers.py        # 辅助函数
├── data/                 # 数据存储目录
│   ├── trade_history.json # 交易历史记录
│   └── trade_summary.json # 交易统计摘要
├── logs/                 # 日志文件目录
├── requirements.txt      # 依赖项列表
├── start_bot.bat         # Windows启动脚本
└── README.md             # 项目说明文档
```

## 注意事项

1. **风险提示**：加密货币交易存在风险，请确保了解相关风险再使用该工具
2. **资金安全**：建议使用小额资金进行测试，确认系统正常运行后再增加资金
3. **网络稳定**：机器人需要稳定的网络连接，网络中断可能导致交易失败
4. **技术支持**：如遇问题，请提交GitHub Issue或联系开发者

## 性能优化

- 调整`config.py`中的`price_check_interval`可改变价格检查频率
- 增加`min_profit_threshold`可减少交易次数，但可能错过一些小额利润机会
- 适当增加`slippage_tolerance`可提高交易成功率，但可能降低实际利润

## 免责声明

本项目仅供学习和研究使用，使用者应自行承担使用该工具进行交易的所有风险。开发者不对因使用该工具导致的任何损失负责。

## 许可证

[MIT License](LICENSE) 