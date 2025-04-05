import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 网络设置
RPC_URL = os.getenv("RPC_URL", "https://monad-testnet.g.alchemy.com/v2/dkEUofCC_DkGE0hb1qfcLosQeneQWmLc")
CHAIN_ID = int(os.getenv("CHAIN_ID", "1") or "1")

# MON(原生代币)的包装地址
WRAPPED_MON = os.getenv("WRAPPED_MON", "0x760afe86e5de5fa0ee542fc7b7b713e1c5425701")

# DEX路由器地址 - 已更新为通过买卖测试的交易所
DEX_ROUTERS = {
    "hakifi": os.getenv("HAKIFI_ROUTER", "0x398ac3b5d6c8279ea32ed05ca2b8331132afcebe"),  # ✅ 测试通过：价格查询、买入、卖出
    # "monorail": os.getenv("MONORAIL_ROUTER", "0xc995498c22a012353fae7ecc701810d673e25794"),  # ❌ 测试失败：WETH地址获取失败
    # "atlantisdex": os.getenv("ATLANTISDEX_ROUTER", "0x3012e9049d05b4b5369d690114d5a5861ebb85cb"),  # ❌ 测试失败：合约执行错误
    "bean": os.getenv("BEAN_ROUTER", "0xca810d095e90daae6e867c19df6d9a8c56db2c89"),  # ✅ 测试通过：价格查询、买入、卖出
    # "kuru": os.getenv("KURU_ROUTER", "0xc80565f78a6e44fb46e1445006f820448840386e"),  # ❌ 测试失败：WETH地址获取失败
    "monda": os.getenv("MONDA_ROUTER", "0xc80585f78a6e44fb46e1445006f820448840386e"),  # ✅ 测试通过：价格查询、买入、卖出
    "octo": os.getenv("OCTO_ROUTER", "0xb6091233aacacba45225a2b2121bbac807af4255"),  # ✅ 测试通过：价格查询、买入、卖出
    # "crystal": os.getenv("CRYSTAL_ROUTER", "0xe98954ed84ac45321b911c6e4e57065358d675cd"),  # ❌ 测试失败：交易在链上执行失败
    # 新增DEX测试结果
    # "pancakeswap": os.getenv("PANCAKESWAP_ROUTER", "0x94d220c58a23ae0c2ee29344b00a30d1c2d9f1bc"),  # ❌ 测试失败：合约执行错误
    "madness": os.getenv("MADNESS_ROUTER", "0x64aff7245ebdaaecaf266852139c67e4d8dba4de"),  # ✅ 测试通过：价格查询、买入、卖出
    #"bubblefi": os.getenv("BUBBLEFI_ROUTER", "0x6c4f91880654a4f4414f50e002f361048433051b"),  # ✅ 测试通过：价格查询、买入、卖出（注意：实际交易时可能出现失败）
    # "reactorfi": os.getenv("REACTORFI_ROUTER", "0xdea70f42a5d04bfde45f27db7c97563814dab15c"),  # ❌ 未测试
    # "uniswap": os.getenv("UNISWAP_ROUTER", "0x3ae6d8a282d67893e17aa70ebffb33ee5aa65893"),  # ❌ 测试失败：合约执行错误
    # "zkswap": os.getenv("ZKSWAP_ROUTER", "0x74a116b1bb7894d3cfbc4b1a12f59ea95f3fff81")  # ❌ 测试失败：合约执行错误
}

# DEX类型配置
# "uniswap_v2": 使用标准Uniswap V2接口 (getAmountsOut等函数)
# "algebra": 使用Algebra/UniswapV3接口 (exactInputSingle等函数)
DEX_TYPES = {
    "hakifi": "uniswap_v2",
    # "monorail": "uniswap_v2",  # 暂时禁用
    # "atlantisdex": "algebra",   # 暂时禁用：合约执行失败
    "bean": "uniswap_v2",
    # "kuru": "uniswap_v2",  # 暂时禁用
    "monda": "uniswap_v2",
    "octo": "uniswap_v2",
    # "crystal": "uniswap_v2",  # 暂时禁用
    # 新增DEX类型 - 通过测试的
    # "pancakeswap": "uniswap_v2",  # 暂时禁用：合约执行失败
    "madness": "uniswap_v2",
    #"bubblefi": "uniswap_v2",
    # "reactorfi": "uniswap_v2",  # 暂时禁用
    # "uniswap": "uniswap_v2",  # 暂时禁用：合约执行失败
    # "zkswap": "uniswap_v2"  # 暂时禁用：合约执行失败
}

# 代币合约地址 - 已更新为实际交易中使用的地址
TOKENS = {
    "MON": None,  # 原生代币
    "USDC": os.getenv("USDC_ADDRESS", "0xf817257fed379853cde0fa4f97ab987181b1e5ea".lower()),  # 已更新为实际交易中使用的地址
}


# 交易对设置
TOKEN_PAIRS = [
    ("MON", "USDC"),
]

# 套利配置
ARBITRAGE_CONFIG = {
    "mon_decimals": 18,  # MON精度
    "price_diff_threshold": 1,  # 最小价格差异阈值（百分比）
    "min_profit_threshold": 0.05,  # 最小利润阈值（单位：输入代币）
    "max_trade_amount": 1,  # 最大交易金额（单位：输入代币）
    "slippage_tolerance": 1.0,  # 滑点容忍度（百分比）
    "price_check_interval": 5,  # 检查价格间隔（秒）
    "gas_price_multiplier": 1.1,  # Gas价格乘数
    "gas_limit": 170000,  # 交易的Gas限制，可根据交易复杂度调整
    "trade_interval": 5,  # 常规套利模式下的交易冷却时间（秒）
}

# 刷交易量模式配置
# 该配置用于控制刷交易量模式的行为，可通过修改enabled参数来启用或禁用该模式
VOLUME_BOOSTING = {
    "enabled": True,        # 是否启用刷交易量模式：True启用，False禁用
    "target_dex": "monda",  # 目标DEX名称，必须是DEX_ROUTERS中列出的交易所之一
    "loss_tolerance": 1.0,  # 亏损容忍度（百分比），最大允许亏损比例，包含gas费用
    "min_trade_amount": 7.0, # 最小交易金额（MON单位），随机交易量的下限
    "max_trade_amount": 10.0, # 最大交易金额（MON单位），随机交易量的上限
    "trade_interval": 5,    # 刷量交易的冷却时间（秒），两次刷量交易之间的最小等待时间
    "gas_limit": 200000,    # 每次交易的Gas限制，刷交易量模式专用
    "gas_price_multiplier": 1.15, # Gas价格乘数，控制交易的燃料价格
    "include_gas_in_calculation": True,  # 是否在计算亏损时包含gas成本 True启用，False禁用
    "volume_limit": 55000.0   # 达到的交易量（USDC单位）限制，达到此值后停止脚本
}

# 日志配置
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file_level": "DEBUG",
    "max_file_size": 10485760,  # 10MB
    "backup_count": 5
}

# 高级设置（仅限开发人员使用）
ADVANCED_CONFIG = {
    "retry_count": 3,  # 交易重试次数
    "retry_interval": 5,  # 重试间隔（秒）
    "web3_timeout": 30,  # Web3连接超时（秒）
    "confirmation_blocks": 1,  # 交易确认块数
    "max_pending_txs": 5,  # 最大待处理交易数
}

# 程序运行目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 合约和ABI文件路径
CONTRACT_PATHS = {
    "router_abi": os.path.join(BASE_DIR, "contracts", "router_abi.json"),
    "erc20_abi": os.path.join(BASE_DIR, "contracts", "erc20_abi.json"),
    "algebra_router_abi": os.path.join(BASE_DIR, "contracts", "algebra_router_abi.json"),
    # 新增DEX的ABI路径
    "atlantisdex_router_abi": os.path.join(BASE_DIR, "contracts", "atlantisdex_router_abi.json"),
    "pancakeswap_router_abi": os.path.join(BASE_DIR, "contracts", "pancakeswap_router_abi.json"),
    "madness_router_abi": os.path.join(BASE_DIR, "contracts", "madness_router_abi.json"),
    "bubblefi_router_abi": os.path.join(BASE_DIR, "contracts", "bubblefi_router_abi.json"),
    "uniswap_router_abi": os.path.join(BASE_DIR, "contracts", "uniswap_router_abi.json"),
    "zkswap_router_abi": os.path.join(BASE_DIR, "contracts", "zkswap_router_abi.json")
}

# 检查必要目录是否存在
for directory in ["logs", "data", "contracts"]:
    os.makedirs(os.path.join(BASE_DIR, directory), exist_ok=True)

# Monad测试网配置
NETWORK_CONFIG = {
    "name": "Monad Testnet",
    "rpc_url": RPC_URL,
    "chain_id": CHAIN_ID,
    "symbol": "MON",
    "explorer": "https://testnet.monadexplorer.com/"
}

# 通知配置
NOTIFICATION_CONFIG = {
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": ""
    }
} 