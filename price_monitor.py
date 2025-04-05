import os
import time
import logging
import asyncio
import concurrent.futures
import sys
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv
import random

from config import DEX_ROUTERS, DEX_TYPES, TOKENS, TOKEN_PAIRS, ARBITRAGE_CONFIG, WRAPPED_MON, VOLUME_BOOSTING
from utils.helpers import get_web3, load_abi, format_address, format_amount, calculate_price_impact, get_deadline

# 设置日志记录 - 修改为只输出到控制台，不记录文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # 移除文件日志处理器
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("price_monitor")

# 确保日志目录存在
os.makedirs("logs", exist_ok=True)

# 加载环境变量
load_dotenv()

# 设置TEST_MODE（如果环境变量中设置了的话）
import os
# 直接设置为False，不再从环境变量读取
TEST_MODE = False

if TEST_MODE:
    logger.info("价格监控器运行在测试模式")
else:
    logger.info("价格监控器运行在正常模式")

class PriceMonitor:
    """
    DEX价格监控类，用于从多个DEX获取代币价格并比较
    """
    def __init__(self):
        self.web3 = get_web3()
        self.router_abi = load_abi("contracts/router_abi.json")
        self.algebra_router_abi = load_abi("contracts/algebra_router_abi.json")
        self.erc20_abi = load_abi("contracts/erc20_abi.json")
        
        # 初始化合约实例
        self.routers = {}
        for dex_name, router_address in DEX_ROUTERS.items():
            try:
                router_address = Web3.to_checksum_address(router_address)
                
                # 根据DEX类型选择正确的ABI
                if dex_name in DEX_TYPES and DEX_TYPES[dex_name] == "algebra":
                    abi = self.algebra_router_abi
                else:
                    abi = self.router_abi
                
                self.routers[dex_name] = self.web3.eth.contract(
                    address=router_address,
                    abi=abi
                )
            except Exception as e:
                logger.error(f"加载 {dex_name} 路由器失败: {e}")
        
        # 初始化代币合约实例
        self.tokens = {}
        self.token_decimals = {}
        self._weth_addresses = {}  # 缓存WETH地址
        
        # 设置所有代币的默认精度，避免调用合约
        # MON(原生代币)精度
        self.token_decimals["MON"] = ARBITRAGE_CONFIG["mon_decimals"]
        
        # 常规ERC20代币，设置默认精度
        for token_name, _ in TOKENS.items():
            if token_name == "MON":
                continue
            # 大多数ERC20代币精度为18，USDC/USDT通常为6
            if token_name in ["USDC", "USDT"]:
                self.token_decimals[token_name] = 6
            else:
                self.token_decimals[token_name] = 18
            
            # 只存储代币地址，不初始化合约
            for token_name, token_address in TOKENS.items():
                if token_name == "MON" or not token_address:
                    continue
                try:
                    # 只存储地址，不初始化合约
                    self.tokens[token_name] = Web3.to_checksum_address(token_address)
                except Exception as e:
                    logger.error(f"处理代币 {token_name} 地址失败: {e}")
    
    async def get_weth_address(self, dex_name):
        """获取指定DEX的WETH地址（即包装后的MON地址）"""
        # 检查缓存
        if dex_name in self._weth_addresses:
            return self._weth_addresses[dex_name]
            
        # 如果配置中有WRAPPED_MON，直接使用
        if WRAPPED_MON:
            # 在debug级别记录WETH地址信息，而不是info级别
            logger.debug(f"使用WRAPPED_MON地址: {WRAPPED_MON}")
            self._weth_addresses[dex_name] = WRAPPED_MON
            return WRAPPED_MON
            
        # 否则尝试从路由器获取
        if dex_name in self.routers:
            try:
                router = self.routers[dex_name]
                
                # 根据DEX类型选择正确的方法获取WETH地址
                if dex_name in DEX_TYPES and DEX_TYPES[dex_name] == "algebra":
                    weth_address = router.functions.WNativeToken().call()
                else:
                    weth_address = router.functions.WETH().call()
                    
                self._weth_addresses[dex_name] = weth_address
                return weth_address
            except Exception as e:
                logger.error(f"获取 {dex_name} WETH地址失败: {e}")
                
        return None
    
    async def get_all_prices(self):
        """
        获取所有交易对在所有DEX上的价格，使用并行处理提高效率
        
        返回:
            dict: 格式为 {(token_in, token_out): {dex_name: price}}
        """
        prices = {}
        
        # 检查是否启用了刷交易量模式
        volume_boosting_enabled = VOLUME_BOOSTING.get("enabled", False)
        
        # 构建需要查询的所有交易对，包括原始交易对和反向交易对
        all_pairs = []
        for token_pair in TOKEN_PAIRS:
            token_in, token_out = token_pair
            # 添加原始交易对
            all_pairs.append((token_in, token_out))
            # 添加反向交易对
            all_pairs.append((token_out, token_in))
            

        
        # 遍历所有交易对(包括反向交易对)
        for token_pair in all_pairs:
            token_in, token_out = token_pair
            pair_prices = {}
            
            # 创建所有DEX的查询任务列表
            price_tasks = []
            dex_names = []
            
            # 确定交易金额
            if volume_boosting_enabled:
                # 在刷交易量模式下使用刷交易量配置的平均交易金额
                max_trade_amount = (VOLUME_BOOSTING["min_trade_amount"] + VOLUME_BOOSTING["max_trade_amount"]) / 2
            else:
                # 常规模式使用常规套利配置
                max_trade_amount = ARBITRAGE_CONFIG["max_trade_amount"]
            
            for dex_name in DEX_ROUTERS:
                # 转换为Wei单位
                decimals = self.token_decimals.get(token_in, 18)
                amount_in = int(max_trade_amount * (10 ** decimals))
                
                # 创建获取价格的任务
                price_tasks.append(self.get_token_price(dex_name, token_in, token_out, amount_in))
                dex_names.append(dex_name)
            
            # 并行执行所有价格查询任务
            price_results = await asyncio.gather(*price_tasks)
            
            # 处理结果
            for i, (price, _, _) in enumerate(price_results):
                dex_name = dex_names[i]
                # 如果价格有效，添加到结果
                if price > 0:
                    pair_prices[dex_name] = price
            
            # 如果至少有一个DEX有该交易对的价格，添加到结果
            if pair_prices:
                prices[(token_in, token_out)] = pair_prices
            else:
                # 保留警告日志，帮助排查问题
                logger.warning(f"交易对 {token_in}->{token_out} 在所有DEX上均无有效价格")
        
        return prices
    
    async def get_token_price(self, dex_name, token_in, token_out, amount_in=None):
        """获取指定DEX上的代币价格"""
        try:
            # 检查路由器是否已加载
            if dex_name not in self.routers:
                logger.error(f"路由器 {dex_name} 未加载")
                return 0, 0, 0
                
            router = self.routers[dex_name]
            
            # 获取代币地址
            token_in_address = TOKENS.get(token_in)
            token_out_address = TOKENS.get(token_out)
            
            # 如果是原生代币MON，获取WETH地址
            if token_in == "MON":
                token_in_address = await self.get_weth_address(dex_name)
            
            if token_out == "MON":
                token_out_address = await self.get_weth_address(dex_name)
                
            # 检查代币地址是否有效
            if not token_in_address or not token_out_address:
                logger.error(f"代币地址无效: token_in={token_in_address}, token_out={token_out_address}")
                return 0, 0, 0
                
            # 将地址转换为checksum格式
            token_in_address = Web3.to_checksum_address(token_in_address)
            token_out_address = Web3.to_checksum_address(token_out_address)
            
            # 如果没有指定输入金额，使用配置中的max_trade_amount
            if amount_in is None:
                max_amount = ARBITRAGE_CONFIG["max_trade_amount"]
                decimals = self.token_decimals.get(token_in, 18)
                amount_in = int(max_amount * (10 ** decimals))
            else:
                # 如果提供了amount_in，计算原始金额用于日志显示
                decimals = self.token_decimals.get(token_in, 18)
                original_amount = amount_in / (10 ** decimals)
            
            # 确定DEX类型
            dex_type = DEX_TYPES.get(dex_name, "uniswap_v2")
            
            # 根据DEX类型使用不同的方法获取价格
            if dex_type == "algebra":
                return await self._get_algebra_dex_price(router, dex_name, token_in, token_out, token_in_address, token_out_address, amount_in)
            else:
                return await self._get_uniswap_v2_price(router, dex_name, token_in, token_out, token_in_address, token_out_address, amount_in)
                
        except Exception as e:
            # 保留错误日志，帮助排查问题
            logger.error(f"获取 {dex_name} 上 {token_in}->{token_out} 价格时出错: {str(e)}")
            if hasattr(e, '__traceback__'):
                import traceback
                logger.error(traceback.format_exc())
            return 0, 0, 0
    
    async def _get_uniswap_v2_price(self, router, dex_name, token_in, token_out, token_in_address, token_out_address, amount_in):
        """使用UniswapV2接口获取价格"""
        try:
            # 构建路径参数
            path = [token_in_address, token_out_address]
            
            # 调用路由器合约的getAmountsOut函数
            amounts = router.functions.getAmountsOut(amount_in, path).call()
            
            # 计算价格
            amount_out = amounts[1]
            
            # 获取代币精度
            token_in_decimals = self.token_decimals.get(token_in, 18)
            token_out_decimals = self.token_decimals.get(token_out, 18)
            
            # 计算价格比率，考虑精度差异
            price = amount_out / amount_in * (10 ** (token_in_decimals - token_out_decimals))
            
            # 获取用于显示的原始输入金额
            original_amount_in = amount_in / (10 ** token_in_decimals)
            
            return price, amount_out, amount_in
            
        except ContractLogicError as e:
            # 保留错误日志
            logger.error(f"合约逻辑错误: {dex_name} {token_in}->{token_out}: {str(e)}")
            return 0, 0, 0
            
        except ValueError as e:
            # 保留错误日志
            logger.error(f"ValueError: {dex_name} {token_in}->{token_out}: {str(e)}")
            return 0, 0, 0
            
        except Exception as e:
            # 保留错误日志
            logger.error(f"获取价格时异常: {dex_name} {token_in}->{token_out}: {str(e)}")
            return 0, 0, 0
    
    async def _get_algebra_dex_price(self, router, dex_name, token_in, token_out, token_in_address, token_out_address, amount_in):
        """使用Algebra接口获取价格"""
        try:
            # 获取pool deployer地址
            pool_deployer = await self.get_pool_deployer(dex_name)
            if not pool_deployer:
                logger.error(f"找不到{dex_name}的pool deployer地址")
                return 0, 0, 0
            
            # 获取当前账户地址作为recipient
            recipient = self.web3.eth.default_account
            
            # 创建ExactInputSingleParams结构
            # 设置deadline
            deadline = int(time.time() + 300)  # 默认5分钟
            
            # 设置最小输出金额(这里设为0，因为只是查询价格)
            amount_out_minimum = 0
                
            # Algebra DEX使用exactInputSingle函数来查询和执行交易
            # 构建参数结构
            params = {
                'tokenIn': token_in_address,
                'tokenOut': token_out_address,
                'deployer': pool_deployer,
                'recipient': recipient,
                'deadline': deadline,
                'amountIn': amount_in,
                'amountOutMinimum': amount_out_minimum,
                'limitSqrtPrice': 0  # 0表示不设置价格限制
            }
            
            # 使用exactInputSingle进行查询
            try:
                # 由于这是只读调用，我们使用call()方法而不是真正发送交易
                amount_out = router.functions.exactInputSingle(params).call()
                
                # 获取代币精度
                token_in_decimals = self.token_decimals.get(token_in, 18)
                token_out_decimals = self.token_decimals.get(token_out, 18)
                
                # 计算价格比率，考虑精度差异
                price = amount_out / amount_in * (10 ** (token_in_decimals - token_out_decimals))
                
                # 获取用于显示的原始输入金额
                original_amount_in = amount_in / (10 ** token_in_decimals)
                
                return price, amount_out, amount_in
                
            except Exception as e:
                # 保留错误日志
                logger.error(f"Algebra exactInputSingle查询失败: {e}")
                return 0, 0, 0
            
        except Exception as e:
            # 保留错误日志
            logger.error(f"Algebra价格查询失败: {e}")
            return 0, 0, 0
    
    async def check_pair_price_difference(self, token_in, token_out):
        """
        检查特定交易对在不同DEX之间的价格差异和套利机会
        
        正确的套利逻辑：
        1. 找到token_in->token_out价格最高的DEX (最佳卖出点)
        2. 计算在该DEX用固定数量token_in能获得多少token_out
        3. 用获得的token_out在所有DEX尝试转换回token_in
        4. 计算每条路径的实际利润（获得的token_in减去原始投入的token_in）
        """
        try:
            # 遍历所有DEX
            dex_prices = {}
            price_tasks = []
            dex_names = []
            
            # 使用实际交易金额
            max_amount = ARBITRAGE_CONFIG["max_trade_amount"]
            token_in_decimals = self.token_decimals.get(token_in, 18)
            amount_in_wei = int(max_amount * (10 ** token_in_decimals))
            
            # 创建所有DEX的查询任务 - 第一步：查询token_in->token_out价格
            for dex_name in DEX_ROUTERS:
                price_tasks.append(self.get_token_price(dex_name, token_in, token_out, amount_in_wei))
                dex_names.append(dex_name)
            
            # 并行执行所有价格查询任务
            price_results = await asyncio.gather(*price_tasks)
            
            # 处理结果
            for i, (price, amount_out, _) in enumerate(price_results):
                dex_name = dex_names[i]
                if price > 0:
                    dex_prices[dex_name] = {
                        "price": price,
                        "amount_out": amount_out,
                        "amount_in": amount_in_wei
                    }
            
            # 如果没有任何DEX有有效价格，返回
            if not dex_prices:
                logger.info(f"没有DEX有 {token_in}->{token_out} 交易对的有效价格")
                return []

            # 找出价格最高的DEX (最佳卖出点)
            best_sell_price = -1
            best_sell_dex = None
            best_output_amount = 0
            
            for dex_name, price_data in dex_prices.items():
                price = price_data["price"]
                amount_out = price_data["amount_out"]
                if price > best_sell_price:
                    best_sell_price = price
                    best_sell_dex = dex_name
                    best_output_amount = amount_out
            
            if best_sell_price <= 0 or best_output_amount <= 0:
                logger.warning(f"最佳卖出价格异常: best_price={best_sell_price}, amount_out={best_output_amount}")
                return []
            
            # 计算卖出token_in后获得的token_out数量（人类可读金额）
            token_out_decimals = self.token_decimals.get(token_out, 18)
            obtained_token_out = best_output_amount / (10 ** token_out_decimals)
            
            logger.info(f"找到最佳卖出DEX: {best_sell_dex}, {max_amount} {token_in} = {obtained_token_out:.6f} {token_out}")
            
            # 第二步：用获得的token_out在各DEX转换回token_in
            token_out_amount_wei = best_output_amount
            
            # 创建所有DEX的查询任务 - 检查用相同数量的token_out能换回多少token_in
            reverse_price_tasks = []
            reverse_dex_names = []
            
            for dex_name in DEX_ROUTERS:
                reverse_price_tasks.append(self.get_token_price(dex_name, token_out, token_in, token_out_amount_wei))
                reverse_dex_names.append(dex_name)
                
            # 并行执行所有反向价格查询任务
            reverse_price_results = await asyncio.gather(*reverse_price_tasks)
            
            # 处理结果，构建套利机会
            opportunities = []
            
            for i, (reverse_price, reverse_amount_out, _) in enumerate(reverse_price_results):
                buy_dex = reverse_dex_names[i]
                
                if reverse_price > 0 and reverse_amount_out > 0:
                    # 计算买回得到的token_in数量
                    final_token_in = reverse_amount_out / (10 ** token_in_decimals)
                    
                    # 计算套利利润
                    profit = final_token_in - max_amount  # 获得的token_in减去初始投入
                    profit_percentage = (profit / max_amount) * 100
                    
                    logger.info(f"套利路径: {best_sell_dex}->{buy_dex} | "
                              f"投入: {max_amount} {token_in} | "
                              f"中间获得: {obtained_token_out:.6f} {token_out} | "
                              f"最终获得: {final_token_in:.6f} {token_in} | "
                              f"利润: {profit_percentage:.2f}%, {profit:.6f} {token_in}")
                    
                    # 只有当利润大于阈值时才添加套利机会
                    if profit > ARBITRAGE_CONFIG["min_profit_threshold"]:
                        opportunities.append({
                            "type": "simple",
                            "token_in": token_in,
                            "token_out": token_out,
                            "token_middle": token_out,  # 在单步套利中，中间代币就是token_out
                            "buy_dex": buy_dex,  # 我们是买回token_in
                            "sell_dex": best_sell_dex,  # 我们是卖出token_in
                            "max_trade_amount": max_amount,
                            "expected_profit": profit,
                            "expected_profit_percentage": profit_percentage,
                            "token_out_amount": obtained_token_out,
                            "final_token_in": final_token_in
                        })
                        
                        logger.info(f"发现套利机会: {best_sell_dex}->{buy_dex}, 预期利润: {profit:.6f} {token_in} ({profit_percentage:.2f}%)")
            
            # 按利润排序
            opportunities.sort(key=lambda x: x["expected_profit"], reverse=True)
            
            return opportunities
            
        except Exception as e:
            logger.error(f"检查 {token_in}->{token_out} 价格差异时出错: {e}")
            return []
    
    async def find_arbitrage_opportunities(self):
        """查找所有可能的套利机会"""
        try:
            all_opportunities = []
            
            # 创建所有交易对的检查任务
            check_tasks = []
            pairs = []
            
            for token_pair in TOKEN_PAIRS:
                token_in, token_out = token_pair
                check_tasks.append(self.check_pair_price_difference(token_in, token_out))
                pairs.append(token_pair)
                
                # 同时检查反向交易对 (例如，除了 MON->USDC，还检查 USDC->MON)
                check_tasks.append(self.check_pair_price_difference(token_out, token_in))
                pairs.append((token_out, token_in))
            
            # 并行执行所有检查任务
            results = await asyncio.gather(*check_tasks)
            
            # 处理结果
            for i, opportunities in enumerate(results):
                token_pair = pairs[i]
                if opportunities:
                    logger.info(f"交易对 {token_pair[0]}->{token_pair[1]} 发现 {len(opportunities)} 个套利机会")
                    all_opportunities.extend(opportunities)
            
            # 按照预期利润排序
            if all_opportunities:
                sorted_ops = sorted(all_opportunities, key=lambda op: op.get("expected_profit", 0), reverse=True)
                
                logger.info(f"总共发现 {len(sorted_ops)} 个套利机会")
                
                # 返回按利润排序的机会列表
                return sorted_ops
            else:
                logger.info("未发现套利机会")
                return []
                
        except Exception as e:
            logger.error(f"查找套利机会时出错: {e}")
            return []
    
    async def find_triangular_arbitrage(self, dex_name, tokens):
        """
        检查在单个DEX内的三角套利机会
        
        参数:
            dex_name (str): DEX名称
            tokens (list): 代币路径，如["MON", "USDC", "USDT"]
            
        返回:
            dict: 套利机会详情或None
        """
        if len(tokens) != 3:
            logger.error("三角套利需要恰好3个代币")
            return None
            
        try:
            # 使用配置中的max_trade_amount
            max_amount = ARBITRAGE_CONFIG["max_trade_amount"]
            token0_decimals = self.token_decimals.get(tokens[0], 18)
            amount_in_wei = int(max_amount * (10 ** token0_decimals))
            
            # 获取三段路径的价格
            price_a_b_info = await self.get_token_price(dex_name, tokens[0], tokens[1], amount_in_wei)
            price_a_b = price_a_b_info[0]
            
            # 使用B的对应金额
            if price_a_b > 0:
                token1_decimals = self.token_decimals.get(tokens[1], 18)
                b_amount_wei = int(max_amount * price_a_b * (10 ** token1_decimals))
                price_b_c_info = await self.get_token_price(dex_name, tokens[1], tokens[2], b_amount_wei)
                price_b_c = price_b_c_info[0]
                
                # 使用C的对应金额
                if price_b_c > 0:
                    token2_decimals = self.token_decimals.get(tokens[2], 18)
                    c_amount_wei = int(b_amount_wei * price_b_c * (10 ** token2_decimals))
                    price_c_a_info = await self.get_token_price(dex_name, tokens[2], tokens[0], c_amount_wei)
                    price_c_a = price_c_a_info[0]
                else:
                    return None
            else:
                return None
            
            if price_a_b <= 0 or price_b_c <= 0 or price_c_a <= 0:
                return None
                
            # 计算三角套利比率
            # 如果乘积 > 1，表示存在套利机会
            triangular_ratio = price_a_b * price_b_c * price_c_a
            profit_percent = (triangular_ratio - 1) * 100
            
            # 如果有利可图
            if profit_percent > ARBITRAGE_CONFIG["price_diff_threshold"]:
                # 计算潜在利润
                max_trade_amount = ARBITRAGE_CONFIG["max_trade_amount"]
                expected_final = max_trade_amount * triangular_ratio
                profit_amount = expected_final - max_trade_amount
                
                path_str = "->".join(tokens) + "->" + tokens[0]
                
                return {
                    "dex": dex_name,
                    "path": path_str,
                    "triangular_ratio": triangular_ratio,
                    "profit_percent": profit_percent,
                    "profit_amount": profit_amount,
                    "max_trade_amount": max_trade_amount
                }
            
            return None
            
        except Exception as e:
            logger.error(f"检查三角套利时出错: {e}")
            return None
    
    def get_price_from_cache(self, prices, token_in, token_out, dex_name):
        """
        从价格缓存中获取特定交易对在特定DEX的价格
        
        参数:
            prices (dict): 价格缓存
            token_in (str): 输入代币
            token_out (str): 输出代币
            dex_name (str): DEX名称
            
        返回:
            float: 价格比率，如果找不到则返回0
        """
        # 先尝试直接查找
        if (token_in, token_out) in prices and dex_name in prices[(token_in, token_out)]:
            return prices[(token_in, token_out)][dex_name]
        
        # 尝试反向查找并计算倒数
        if (token_out, token_in) in prices and dex_name in prices[(token_out, token_in)]:
            reverse_price = prices[(token_out, token_in)][dex_name]
            if reverse_price > 0:
                return 1 / reverse_price
                
        return 0
    
    async def monitor_prices(self):
        """
        持续监控价格并寻找套利机会
        """
        while True:
            try:
                logger.info("开始监控DEX价格...")
                
                # 寻找简单套利机会
                simple_opportunities = await self.find_arbitrage_opportunities()
                
                # 打印套利机会
                if simple_opportunities:
                    logger.info(f"发现 {len(simple_opportunities)} 个跨DEX套利机会")
                    for idx, opp in enumerate(simple_opportunities[:5], 1):  # 只显示前5个
                        max_amount = opp.get("max_trade_amount", ARBITRAGE_CONFIG["max_trade_amount"])
                        logger.info(f"机会 {idx}: {opp['token1']}-{opp['token2']} | "
                                   f"买入: {opp['buy_dex']} (单价: {opp['buy_price']:.6f}) | "
                                   f"卖出: {opp['sell_dex']} (单价: {opp['sell_price']:.6f}) | "
                                   f"交易量: {max_amount} {opp['token1']} | "
                                   f"价差: {opp['price_diff_percent']:.2f}%")
                else:
                    logger.info("未发现跨DEX套利机会")
                
                # 等待下一轮监控
                await asyncio.sleep(ARBITRAGE_CONFIG["price_check_interval"])
                
            except Exception as e:
                logger.error(f"价格监控出错: {e}")
                await asyncio.sleep(10)  # 出错后等待一段时间再重试

    def find_volume_boosting_opportunities(self, trading_pairs, prices):
        """
        找到跨DEX的刷交易量机会：在最优价格DEX卖出获取最多USDC，在目标DEX买回MON
        
        Args:
            trading_pairs (list): 交易对列表，如["MON-USDC"]
            prices (dict): 价格数据，格式为 {(token_in, token_out): {dex_name: price}}
            
        Returns:
            list: 刷交易量交易机会列表
        """
        # 检查VOLUME_BOOSTING是否启用
        if not VOLUME_BOOSTING.get("enabled", False):
            logger.info("刷交易量模式未启用，跳过刷交易量机会查找")
            return []
            
        # 获取刷交易量配置
        target_dex = VOLUME_BOOSTING["target_dex"]
        loss_tolerance = VOLUME_BOOSTING["loss_tolerance"]
        min_trade_amount = VOLUME_BOOSTING["min_trade_amount"]
        max_trade_amount = VOLUME_BOOSTING["max_trade_amount"]
        
        logger.info(f"开始查找跨DEX刷交易量机会 - 目标买回DEX: {target_dex}, 亏损容忍度: {loss_tolerance}%")
        
        # 验证目标DEX是否有效
        if target_dex not in DEX_ROUTERS:
            logger.error(f"指定的目标DEX {target_dex} 不存在于配置中")
            return []
            
        volume_boosting_opportunities = []
        
        # 为每个交易对提取token_in和token_out
        for pair_str in trading_pairs:
            # 解析交易对
            parts = pair_str.split("-")
            if len(parts) != 2:
                logger.error(f"交易对格式错误: {pair_str}")
                continue
                
            token_in, token_out = parts
            
            # 选择交易量策略：随机交易金额
            trade_amount = random.uniform(min_trade_amount, max_trade_amount)
            trade_amount = round(trade_amount, 2)  # 保留两位小数
            
            logger.info(f"交易对 {token_in}-{token_out} 随机交易金额: {trade_amount} {token_in}")
            
            # 1. 找到最佳卖出点（获得最多token_out的DEX）
            best_sell_dex = None
            best_sell_price = 0
            best_output_amount = 0
            
            # 正向交易对查询价格: token_in -> token_out (MON -> USDC)
            forward_key = (token_in, token_out)
            if forward_key not in prices:
                logger.warning(f"价格数据中找不到正向交易对 {token_in}->{token_out}")
                continue
                
            # 找出卖出MON获得最多USDC的DEX
            for dex_name, price in prices[forward_key].items():
                if price > best_sell_price:
                    best_sell_price = price
                    best_sell_dex = dex_name
            
            if not best_sell_dex:
                logger.warning(f"交易对 {token_in}->{token_out} 未找到有效的卖出DEX")
                continue
                
            # 计算在最佳DEX卖出能获得的token_out数量
            best_output_amount = trade_amount * best_sell_price
            
            logger.info(f"最佳卖出DEX: {best_sell_dex}, 价格: {best_sell_price:.6f}, {trade_amount} {token_in} = {best_output_amount:.6f} {token_out}")
            
            # 2. 在目标DEX(monda)上用获得的token_out买回token_in
            # 反向交易对查询价格: token_out -> token_in (USDC -> MON)
            reverse_key = (token_out, token_in)
            
            # 检查反向交易对数据是否存在
            if reverse_key not in prices:
                logger.warning(f"价格数据中找不到反向交易对 {token_out}->{token_in}")
                continue
                
            # 检查目标DEX在反向交易对中是否有价格数据
            if target_dex not in prices[reverse_key]:
                logger.warning(f"在目标DEX {target_dex} 上找不到反向交易对 {token_out}->{token_in} 的价格")
                continue
                
            # 目标DEX上的买回价格
            target_buy_price = prices[reverse_key][target_dex]
            
            # 计算在目标DEX上能买回多少token_in
            final_token_in = best_output_amount * target_buy_price
            
            # 3. 计算损益
            profit = final_token_in - trade_amount
            profit_percentage = (profit / trade_amount) * 100
            
            logger.info(f"跨DEX刷交易量路径: {best_sell_dex}->{target_dex} | "
                       f"初始: {trade_amount} {token_in} | "
                       f"中间: {best_output_amount:.6f} {token_out} | "
                       f"最终: {final_token_in:.6f} {token_in} | "
                       f"损益: {profit_percentage:.2f}%")
            
            # 4. 检查损失是否在容忍范围内
            if profit_percentage >= -loss_tolerance:
                logger.info(f"找到合适的刷交易量机会: {best_sell_dex}->{target_dex}, 损益: {profit_percentage:.2f}%")
                
                volume_boosting_opportunity = {
                    "type": "volume_boosting",
                    "token_in": token_in,
                    "token_out": token_out,
                    "sell_dex": best_sell_dex,  # 最佳卖出DEX
                    "buy_dex": target_dex,      # 目标买回DEX
                    "expected_profit": profit,   # 预期损益（通常为负数）
                    "expected_profit_percentage": profit_percentage,  # 预期损益百分比
                    "token_out_amount": best_output_amount,
                    "final_token_in": final_token_in,
                    "max_trade_amount": trade_amount  # 使用随机生成的交易金额
                }
                
                volume_boosting_opportunities.append(volume_boosting_opportunity)
            else:
                logger.info(f"刷交易量损失 {-profit_percentage:.2f}% 超出容忍范围 {loss_tolerance}%，跳过")
                
        # 按损益从高到低排序（优先选择亏损最小的）
        if volume_boosting_opportunities:
            volume_boosting_opportunities.sort(key=lambda x: x["expected_profit_percentage"], reverse=True)
            logger.info(f"总共找到 {len(volume_boosting_opportunities)} 个符合条件的刷交易量机会")
        else:
            logger.info("未找到符合条件的刷交易量机会")
            
        return volume_boosting_opportunities

    async def get_pool_deployer(self, dex_name):
        """获取Algebra类型DEX的pool deployer地址"""
        try:
            if dex_name not in self.routers:
                logger.error(f"路由器 {dex_name} 未加载")
                return None
                
            router = self.routers[dex_name]
            
            # 尝试调用poolDeployer方法
            try:
                pool_deployer = router.functions.poolDeployer().call()
                return pool_deployer
            except Exception as e:
                logger.error(f"获取 {dex_name} poolDeployer失败: {e}")
                return None
        except Exception as e:
            logger.error(f"获取pool deployer时出错: {e}")
            return None

# 测试运行
async def main():
    monitor = PriceMonitor()
    await monitor.monitor_prices()

if __name__ == "__main__":
    asyncio.run(main()) 