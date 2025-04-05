import os
import sys
import time
import json
import logging
import asyncio
import random
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
from price_monitor import PriceMonitor
from transaction_executor import TransactionExecutor
from utils.helpers import format_amount, get_web3
from config import (
    DEX_ROUTERS,
    DEX_TYPES,
    TOKENS,
    TOKEN_PAIRS,
    ARBITRAGE_CONFIG,
    VOLUME_BOOSTING,
    LOG_CONFIG
)

# 创建logs和data目录（如果不存在）
os.makedirs('logs', exist_ok=True)
os.makedirs('data', exist_ok=True)

# 配置日志
import codecs

# 尝试修复Windows控制台的编码问题
if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# 设置日志处理 - 修改为只输出到控制台，不记录文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # 移除文件日志处理器
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("arbitrage_bot")

# 解决Windows控制台中的中文和特殊字符显示问题
# sys.stdout.reconfigure(encoding='utf-8')

class ArbitrageBot:
    def __init__(self):
        """初始化套利机器人"""
        # 加载环境变量
        load_dotenv()
        
        # 连接到RPC
        self.web3 = get_web3()
        
        # 初始化价格监控器
        self.price_monitor = PriceMonitor()
        
        # 初始化交易执行器
        self.transaction_executor = TransactionExecutor()
        
        # 交易历史
        self.trade_history = []
        
        # 添加交易量统计
        self.total_volume_usdc = 0.0  # 跟踪USDC交易量
        
        # 套利统计
        self.stats = {
            "total_trades": 0,
            "successful_trades": 0,
            "failed_trades": 0,
            "total_profit": 0,
            "total_gas_cost": 0,
            "start_time": datetime.now().isoformat()
        }
        
        # 加载现有交易记录
        self._load_trade_history()
        
        # 初始化交易冷却时间
        self.last_trade_time = 0
        # 从配置读取交易冷却时间
        self.min_trade_interval = ARBITRAGE_CONFIG.get("trade_interval", 30)  # 默认30秒

    
    def _load_trade_history(self):
        """加载之前的交易历史记录"""
        try:
            trade_file = "data/trade_history.json"
            if os.path.exists(trade_file):
                with open(trade_file, "r") as f:
                    self.trade_history = json.load(f)
                    
                    # 更新统计数据
                    for trade in self.trade_history:
                        self.stats["total_trades"] += 1
                        if trade.get("status") == "success":
                            self.stats["successful_trades"] += 1
                            self.stats["total_profit"] += trade.get("profit", 0)
                        else:
                            self.stats["failed_trades"] += 1
        except Exception as e:
            logger.error(f"加载交易历史失败: {e}")
    
    async def check_and_save_liquidity(self):
        """检查并保存流动性情况"""

        
        # 保存可交易的代币对
        valid_token_pairs = []
        
        # 检查是否启用了刷交易量模式
        volume_boosting_enabled = VOLUME_BOOSTING.get("enabled", False)
        
        # 设置测试金额，减少价格影响
        if volume_boosting_enabled:
            # 使用刷交易量模式的最大交易金额
            test_amount = VOLUME_BOOSTING["max_trade_amount"]
            logger.info(f"刷交易量模式使用交易金额: {test_amount} MON")
        else:
            # 使用常规套利的最大交易金额
            test_amount = ARBITRAGE_CONFIG["max_trade_amount"]
            logger.info(f"常规套利模式使用交易金额: {test_amount} MON")
            
        test_amount_wei = int(test_amount * (10 ** 18))  # 转换为wei
        
        # 获取有效的DEX列表
        active_dexes = []
        for dex_name, router_address in DEX_ROUTERS.items():
            try:
                # 验证路由器地址是否有效
                if not Web3.is_address(router_address):
                    continue
                    
                # 检查路由器是否已加载
                if dex_name not in self.price_monitor.routers:
                    continue
                    
                active_dexes.append(dex_name)
            except Exception as e:
                logger.error(f"验证DEX {dex_name} 时出错: {e}")
        
        if not active_dexes:
            logger.error("没有有效的DEX路由器，无法检查流动性")
            return []
            
        # 检查每个代币对
        for token_pair in TOKEN_PAIRS:
            token_in, token_out = token_pair
            
            # 检查代币地址是否有效
            token_in_address = TOKENS.get(token_in)
            token_out_address = TOKENS.get(token_out)
            
            if token_in == "MON":
                # 获取WETH地址
                token_in_address = await self.price_monitor.get_weth_address(active_dexes[0])
                
            if token_out == "MON":
                # 获取WETH地址
                token_out_address = await self.price_monitor.get_weth_address(active_dexes[0])
            
            if not token_in_address or not token_out_address:
                logger.error(f"代币地址无效: {token_in}={token_in_address}, {token_out}={token_out_address}")
                continue
                
            # 创建一个包含该交易对在各DEX上的价格的字典
            dex_prices = {}
            
            # 获取该交易对在各DEX上的价格
            for dex_name in active_dexes:
                try:
                    # 使用测试金额检查价格
                    price_info = await self.price_monitor.get_token_price(dex_name, token_in, token_out, test_amount_wei)
                    price, amount_out, amount_in = price_info
                    
                    if price > 0:
                        dex_prices[dex_name] = price
                        valid_token_pairs.append((token_in, token_out))
                    else:
                        logger.warning(f"[失败] {dex_name} 上 {token_in}->{token_out} 价格为零，可能没有流动性")
                except Exception as e:
                    logger.error(f"获取 {dex_name} 上 {token_in}->{token_out} 价格时出错: {e}")
            
            # 如果至少有一个DEX有价格，将其添加到有效交易对
            if dex_prices:
                dex_list = ", ".join([f"{dex}: {price:.6f}" for dex, price in dex_prices.items()])

            else:
                logger.info(f"交易对 {token_in}->{token_out} 没有流动性")
        
        return valid_token_pairs
    
    async def find_and_execute_arbitrage(self):
        """查找并执行套利机会"""
        try:
            # 记录开始时间
            start_time = time.time()
            
            # 检查是否启用了刷交易量模式
            volume_boosting_enabled = VOLUME_BOOSTING.get("enabled", False)
            target_dex = VOLUME_BOOSTING.get("target_dex", "")
            
            if volume_boosting_enabled:
                # 减少日志输出，保留关键信息
                logger.info(f"刷交易量模式已启用 - 目标DEX: {target_dex}")
                
                # 检查上次交易时间
                current_time = time.time()
                time_since_last_trade = current_time - self.last_trade_time
                trade_interval = VOLUME_BOOSTING.get("trade_interval", 60)
                
                if time_since_last_trade < trade_interval:
                    # 优化输出，只有在等待时间较长时才显示日志
                    if trade_interval - time_since_last_trade > 5:
                        logger.info(f"等待刷交易量冷却时间结束，还需 {int(trade_interval - time_since_last_trade)} 秒")
                    return
                
                # 检查是否已达到USDC交易量限制
                if self.total_volume_usdc >= VOLUME_BOOSTING["volume_limit"]:
                    logger.info(f"已达到USDC交易量限制: {self.total_volume_usdc:.2f} USDC >= {VOLUME_BOOSTING['volume_limit']:.2f} USDC")
                    logger.info("根据配置，交易将停止。如需继续交易，请调整VOLUME_BOOSTING中的volume_limit参数")
                    # 保存交易历史并退出
                    self.save_trade_history()
                    # 终止程序
                    logger.info("程序即将退出...")
                    sys.exit(0)
                
                loss_tolerance = VOLUME_BOOSTING["loss_tolerance"]
                volume_limit = VOLUME_BOOSTING["volume_limit"]
                
                logger.info(f"刷交易量模式已启用，目标DEX: {target_dex}，亏损容忍度: {loss_tolerance}%")
                logger.info(f"当前已累计USDC交易量: {self.total_volume_usdc:.2f} USDC，交易量限制: {volume_limit:.2f} USDC")
                logger.info("只会执行刷交易量模式，不会执行常规套利")
            else:
                logger.info("刷交易量模式已禁用，将执行常规套利模式")
            
            logger.info("开始寻找交易机会...")
            
            # 获取所有交易对的价格
            all_prices = await self.price_monitor.get_all_prices()
            
            # 检查有效的交易对
            valid_pairs = await self.check_and_save_liquidity()
            
            if not valid_pairs:
                return
            
            # 对每个交易对，立即检查并执行
            for token_in, token_out in valid_pairs:
                try:
                    # 检查是否应该继续执行
                    current_task = asyncio.current_task()
                    if current_task and current_task.cancelled():
                        logger.info("套利任务被取消")
                        break
                    
                    opportunities = []
                    
                    # 根据模式选择不同的策略
                    if volume_boosting_enabled:
                        # 如果启用了刷交易量模式，只执行刷交易量策略
                        logger.info(f"尝试刷交易量模式: {token_in}->{token_out}")
                        
                        # 构建交易对字符串，格式为"MON-USDC"
                        trading_pair = f"{token_in}-{token_out}"
                        trading_pairs = [trading_pair]
                        
                        logger.info(f"查询刷交易量机会，交易对: {trading_pairs}")
                        
                        # 不再使用await，因为这是同步方法
                        opportunities = self.price_monitor.find_volume_boosting_opportunities(trading_pairs, all_prices)
                        
                        if opportunities:
                            logger.info(f"找到刷交易量机会: {len(opportunities)}个")
                        else:
                            logger.info(f"未找到符合条件的刷交易量机会，跳过")
                            continue  # 如果没找到刷交易量机会，直接跳过，不执行常规套利
                    else:
                        # 如果刷交易量模式未启用，执行常规套利策略
                        logger.info(f"检查常规套利机会: {token_in}->{token_out}")
                        opportunities = await self.price_monitor.check_pair_price_difference(token_in, token_out)
                        
                        if opportunities:
                            logger.info(f"找到常规套利机会: {len(opportunities)}个")
                        else:
                            logger.info(f"未找到套利机会")
                            continue
                    
                    # 如果找到交易机会，选择最佳的执行
                    if opportunities:
                        # 区分常规套利和刷交易量机会
                        if volume_boosting_enabled:
                            # 刷交易量模式处理逻辑
                            best_opportunity = opportunities[0]  # 刷交易量模式已经筛选了最佳机会
                            logger.info(f"刷交易量机会: {best_opportunity['sell_dex']}->{best_opportunity['buy_dex']} | "
                                      f"预期损益: {best_opportunity['expected_profit']:.6f} {token_in} "
                                      f"({best_opportunity['expected_profit_percentage']:.2f}%)")
                            
                            # 刷交易量模式使用loss_tolerance参数，不使用最小利润阈值
                            expected_profit = best_opportunity.get("expected_profit", 0)
                            expected_profit_percentage = best_opportunity.get("expected_profit_percentage", 0)
                            loss_tolerance = VOLUME_BOOSTING.get("loss_tolerance", 1.0)
                            
                            # 计算gas成本以便记录，不必用于决策
                            gas_limit = VOLUME_BOOSTING.get("gas_limit", 170000)
                            gas_price = self.web3.eth.gas_price
                            estimated_gas_cost_wei = gas_limit * gas_price * 2  # 两笔交易
                            estimated_gas_cost = float(self.web3.from_wei(estimated_gas_cost_wei, 'ether'))
                            
                            # 仅在VOLUME_BOOSTING.include_gas_in_calculation为True时考虑gas
                            include_gas = VOLUME_BOOSTING.get("include_gas_in_calculation", True)
                            
                            # 不直接检查利润，因为刷交易量模式允许亏损
                            if include_gas:
                                profit_after_gas = expected_profit - estimated_gas_cost
                                profit_percentage_after_gas = (profit_after_gas / float(best_opportunity.get("max_trade_amount", 10.0))) * 100
                                
                                logger.info(f"预估gas成本: {estimated_gas_cost:.6f} {token_in}")
                                logger.info(f"考虑gas后预期损益: {profit_after_gas:.6f} {token_in} ({profit_percentage_after_gas:.2f}%)")
                                
                                # 如果亏损超过容忍度，则跳过
                                if profit_percentage_after_gas < -loss_tolerance:
                                    logger.warning(f"预期亏损 {-profit_percentage_after_gas:.2f}% 超出容忍度 {loss_tolerance}%，跳过此机会")
                                    continue
                            else:
                                # 不考虑gas，仅检查交易本身的损益
                                if expected_profit_percentage < -loss_tolerance:
                                    logger.warning(f"预期亏损 {-expected_profit_percentage:.2f}% 超出容忍度 {loss_tolerance}%，跳过此机会")
                                    continue
                                    
                            # 刷交易量模式使用VOLUME_BOOSTING中配置的交易间隔
                            min_interval = VOLUME_BOOSTING["trade_interval"]
                            logger.info(f"刷交易量模式使用交易间隔: {min_interval}秒")
                            
                            # 如果距离上次交易时间不足最小间隔，则等待
                            current_time = time.time()
                            if current_time - self.last_trade_time < min_interval:
                                wait_time = min_interval - (current_time - self.last_trade_time)
                                logger.info(f"等待交易冷却时间: {wait_time:.1f}秒")
                                await asyncio.sleep(wait_time)
                            
                            # 执行交易
                            await self.execute_cross_dex_arbitrage(best_opportunity)
                            
                        else:
                            # 常规套利模式处理逻辑
                            # 按照利润排序
                            opportunities.sort(key=lambda x: x.get("expected_profit", 0), reverse=True)
                            best_opportunity = opportunities[0]
                            logger.info(f"最佳套利路径: {best_opportunity['sell_dex']}->{best_opportunity['buy_dex']} | "
                                      f"预期利润: {best_opportunity['expected_profit']:.6f} {token_in} "
                                      f"({best_opportunity['expected_profit_percentage']:.2f}%)")
                            
                            # 套利模式需要确保预期利润超过gas成本和最小阈值
                            gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 170000)
                            gas_price = self.web3.eth.gas_price
                            estimated_gas_cost_wei = gas_limit * gas_price * 2  # 两笔交易
                            estimated_gas_cost = float(self.web3.from_wei(estimated_gas_cost_wei, 'ether'))
                            expected_profit = best_opportunity.get("expected_profit", 0)
                            
                            # 套利模式使用min_profit_threshold参数
                            min_profit_threshold = ARBITRAGE_CONFIG.get("min_profit_threshold", 0.05)
                            profit_after_gas = expected_profit - estimated_gas_cost
                            
                            if profit_after_gas <= min_profit_threshold:
                                logger.warning(f"预期净利润 {profit_after_gas:.6f} {token_in} 不足最小阈值 {min_profit_threshold} {token_in}，跳过此机会")
                                continue
                            
                            logger.info(f"预估gas成本: {estimated_gas_cost:.6f} {token_in}，预期净利润: {profit_after_gas:.6f} {token_in}")
                            
                            # 常规套利模式使用ARBITRAGE_CONFIG中配置的交易间隔
                            min_interval = self.min_trade_interval
                            logger.info(f"常规套利模式使用交易间隔: {min_interval}秒")
                            
                            # 如果距离上次交易时间不足最小间隔，则等待
                            current_time = time.time()
                            if current_time - self.last_trade_time < min_interval:
                                wait_time = min_interval - (current_time - self.last_trade_time)
                                logger.info(f"等待交易冷却时间: {wait_time:.1f}秒")
                                await asyncio.sleep(wait_time)
                            
                            # 执行交易
                            await self.execute_cross_dex_arbitrage(best_opportunity)
                        
                        # 更新最后交易时间
                        self.last_trade_time = time.time()
                        
                        # 保存交易历史
                        self.save_trade_history()
                        
                        # 执行一次成功的交易后，等待交易确认并继续下一轮监控
                        logger.info("等待交易确认...")
                        await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"执行交易时发生错误: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            # 减少执行结束时的冗余输出
            end_time = time.time()
            # 只有当执行时间超过一定阈值才输出耗时日志
            if end_time - start_time > 1.0:
                logger.info(f"交易监控完成，耗时: {end_time - start_time:.2f}秒")
        
        except Exception as e:
            logger.error(f"执行交易监控时发生错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def execute_cross_dex_arbitrage(self, opportunity):
        """执行跨DEX套利"""
        try:
            volume_boosting = VOLUME_BOOSTING.get("enabled", False)
            is_volume_boosting = volume_boosting and opportunity.get("type", "") == "volume_boosting"
            
            # 获取亏损容忍度（仅刷交易量模式使用）
            loss_tolerance = 0
            if is_volume_boosting:
                loss_tolerance = float(VOLUME_BOOSTING.get("loss_tolerance", 2.0))
                logger.info(f"刷交易量模式，亏损容忍度: {loss_tolerance}%")
            
            # 解析套利机会
            token_in = opportunity.get('token_in')
            token_out = opportunity.get('token_out')
            buy_dex = opportunity.get('buy_dex')
            sell_dex = opportunity.get('sell_dex')
            amount_in = float(opportunity.get('max_trade_amount', 0))
            expected_profit_pct = opportunity.get('expected_profit_percentage', 0)
            
            # 常规套利需要额外提取token_middle
            if not is_volume_boosting:
                token_middle = opportunity.get('token_middle', token_out)  # 如果不存在，使用token_out作为中间代币
                expected_profit = opportunity.get('expected_profit', 0)
            else:
                token_middle = token_out  # 刷交易量模式中，token_out是中间代币
                expected_profit = 0  # 刷交易量模式无需计算实际利润
            
            # 记录套利信息 - 简化输出，但保留关键信息
            if is_volume_boosting:
                logger.info(f"执行刷交易量操作：{token_in}→{token_out}→{token_in}")
                logger.info(f"卖出DEX: {sell_dex}, 买回DEX: {buy_dex}, 预期利润率: {expected_profit_pct:.2f}%")
            else:
                logger.info(f"执行套利：{token_in}→{token_middle}→{token_in}")
                logger.info(f"卖出DEX: {sell_dex}, 买回DEX: {buy_dex}, 预期利润: {expected_profit:.6f} {token_in} ({expected_profit_pct:.2f}%)")
            
            # 获取token_in的精度
            if token_in == "MON":
                token_in_decimals = 18  # MON精度
            else:
                token_in_decimals = await self.transaction_executor.get_token_decimals(token_in)
            
            # 获取交易前的初始余额
            if is_volume_boosting:
                initial_balance = await self.transaction_executor.get_token_balance(token_in)
                logger.info(f"交易前{token_in}余额: {initial_balance}")
            else:
                initial_balance = await self.transaction_executor.get_token_balance(token_in)
                logger.info(f"交易前{token_in}余额: {initial_balance}")
            
            # 计算需要的token_in数量
            amount_in_wei = int(amount_in * (10 ** token_in_decimals))
            
            if is_volume_boosting:
                # 刷交易量模式：先在sell_dex卖出token_in获得token_out(USDC)
                logger.info(f"步骤1: 在 {sell_dex} 卖出 {token_in} 获得 {token_out}")
                buy_result, buy_tx_hash, token_middle_amount = await self.transaction_executor.swap_tokens(
                    sell_dex, token_in, token_out, amount_in_wei, 0, None, is_volume_boosting
                )
            else:
                # 常规套利模式：在sell_dex卖出token_in获得token_middle
                logger.info(f"步骤1: 在 {sell_dex} 卖出 {token_in} 获得 {token_middle}")
                buy_result, buy_tx_hash, token_middle_amount = await self.transaction_executor.swap_tokens(
                    sell_dex, token_in, token_middle, amount_in_wei, 0, None, is_volume_boosting
                )
            
            if not buy_result:
                logger.error(f"步骤1交易失败")
                return False, {"profit": 0, "profit_pct": 0}
                
            logger.info(f"步骤1交易成功，获得 {token_middle_amount} {token_middle if not is_volume_boosting else token_out}")
            
            # 获取中间代币的精度
            middle_token_name = token_out if is_volume_boosting else token_middle
            if middle_token_name == "MON":
                token_middle_decimals = 18  # MON精度
            else:
                token_middle_decimals = await self.transaction_executor.get_token_decimals(middle_token_name)
                
            # 计算中间代币的数量（以wei为单位）
            token_middle_amount_wei = int(token_middle_amount * (10 ** token_middle_decimals))
            
            if is_volume_boosting:
                # 刷交易量模式：在buy_dex使用获得的token_out(USDC)买回token_in(MON)
                logger.info(f"步骤2: 在 {buy_dex} 用 {token_out} 买回 {token_in}")
                sell_result, sell_tx_hash, token_out_amount = await self.transaction_executor.swap_tokens(
                    buy_dex, token_out, token_in, token_middle_amount_wei, 0, None, is_volume_boosting
                )
                final_token_name = token_in  # 最终获得的是token_in(MON)
            else:
                # 常规套利模式：在buy_dex用token_middle买回token_in
                logger.info(f"步骤2: 在 {buy_dex} 用 {token_middle} 买回 {token_in}")
                sell_result, sell_tx_hash, token_out_amount = await self.transaction_executor.swap_tokens(
                    buy_dex, token_middle, token_in, token_middle_amount_wei, 0, None, is_volume_boosting
                )
                final_token_name = token_in  # 最终获得的是token_in
            
            if not sell_result:
                logger.error(f"步骤2交易失败")
                return False, {"profit": 0, "profit_pct": 0}
                
            logger.info(f"步骤2交易成功，获得 {token_out_amount} {final_token_name}")
            
            # 获取交易后的最终余额
            final_balance = await self.transaction_executor.get_token_balance(final_token_name)
            
            # 计算实际利润 - 使用交易前后的真实余额差额
            if is_volume_boosting:
                # 刷交易量模式下，计算相同代币（token_in）的差值
                profit = float(final_balance - initial_balance)
            else:
                # 套利模式下，如果初始和最终代币不同，直接用交易后的余额减去投入金额
                if token_in != final_token_name:
                    profit = float(final_balance - amount_in)
                else:
                    profit = float(final_balance - initial_balance)
            
            profit_pct = (profit / float(amount_in)) * 100
            
            # 估算gas费用（MON）
            gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 200000)
            if is_volume_boosting:
                gas_limit = VOLUME_BOOSTING.get("gas_limit", 170000)
                include_gas = VOLUME_BOOSTING.get("include_gas_in_calculation", True)
            else:
                include_gas = True
                
            # 计算gas成本
            gas_price = self.web3.eth.gas_price
            gas_price_gwei = self.web3.from_wei(gas_price, 'gwei')
            estimated_gas_cost_wei = gas_limit * gas_price * 2  # 两笔交易
            estimated_gas_cost = float(self.web3.from_wei(estimated_gas_cost_wei, 'ether'))
            
            logger.info(f"gas价格: {gas_price_gwei} Gwei, 估算总gas: {estimated_gas_cost:.6f} MON")
            
            # 计算净利润
            trade_record = {}
            
            if is_volume_boosting:
                logger.info(f"刷交易量模式交易完成!")
                if include_gas:
                    actual_profit = float(profit - estimated_gas_cost)
                    actual_profit_percentage = (actual_profit / float(amount_in)) * 100
                    logger.info(f"初始{token_in}余额: {initial_balance}")
                    logger.info(f"最终{token_in}余额: {final_balance}")
                    logger.info(f"实际损益: {profit} {token_in} ({profit_pct:.2f}%)")
                    logger.info(f"估算gas: {estimated_gas_cost:.6f} {token_in}")
                    logger.info(f"净损益: {actual_profit} {token_in} ({actual_profit_percentage:.2f}%)")
                    
                    # 检查亏损是否超出容忍度
                    if profit_pct < 0 and abs(profit_pct) > float(loss_tolerance):
                        logger.warning(f"亏损 {-profit_pct:.2f}% 超出容忍度 {loss_tolerance}%")
                    
                    if actual_profit_percentage < 0 and abs(actual_profit_percentage) > float(loss_tolerance):
                        logger.warning(f"实际亏损 {-actual_profit_percentage:.2f}% 超出容忍度 {loss_tolerance}%")
                    
                    # 更新USDC交易量统计
                    if token_out == "USDC":
                        self.total_volume_usdc += token_middle_amount
                        logger.info(f"已累计USDC交易量: {self.total_volume_usdc:.2f} USDC")
                else:
                    logger.info(f"初始{token_in}余额: {initial_balance}")
                    logger.info(f"最终{token_in}余额: {final_balance}")
                    logger.info(f"实际损益: {profit} {token_in} ({profit_pct:.2f}%)")
                    logger.info(f"不计gas的损益")
                    
                    # 检查亏损是否超出容忍度
                    if profit_pct < 0 and abs(profit_pct) > float(loss_tolerance):
                        logger.warning(f"亏损 {-profit_pct:.2f}% 超出容忍度 {loss_tolerance}%")
                    
                    # 更新USDC交易量统计
                    if token_out == "USDC":
                        self.total_volume_usdc += token_middle_amount
                        logger.info(f"已累计USDC交易量: {self.total_volume_usdc:.2f} USDC")
            else:
                logger.info(f"套利交易完成!")
                logger.info(f"初始{token_in}余额: {initial_balance}")
                logger.info(f"最终{token_in}余额: {final_balance}")
                logger.info(f"实际利润: {profit} {token_in} ({profit_pct:.2f}%)")
                logger.info(f"估算gas: {estimated_gas_cost:.6f} {token_in}")
                
                actual_profit = float(profit - estimated_gas_cost)
                actual_profit_percentage = (actual_profit / float(amount_in)) * 100
                logger.info(f"净利润: {actual_profit} {token_in} ({actual_profit_percentage:.2f}%)")
            
            # 记录交易到历史记录
            trade_record = {
                "timestamp": int(time.time()),
                "type": "volume_boosting" if is_volume_boosting else "arbitrage",
                "token_in": token_in,
                "token_out": final_token_name,
                "sell_dex": sell_dex,
                "buy_dex": buy_dex,
                "amount": amount_in,
                "initial_balance": float(initial_balance),
                "final_balance": float(final_balance),
                "token_out_amount": token_out_amount,
                "token_middle_amount": token_middle_amount,
                "profit": float(profit),
                "profit_percentage": float(profit_pct),
                "estimated_gas": float(estimated_gas_cost),
                "net_profit": float(actual_profit if 'actual_profit' in locals() else profit),
                "sell_tx_hash": sell_tx_hash,
                "buy_tx_hash": buy_tx_hash,
                "status": "success"
            }
            
            self.trade_history.append(trade_record)
            
            # 更新统计信息
            self.stats["total_trades"] += 1
            self.stats["successful_trades"] += 1
            self.stats["total_profit"] += profit
            
            return True, trade_record
            
        except Exception as e:
            logger.error(f"执行套利交易时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            try:
                # 尝试记录失败交易
                trade_record = {
                    "timestamp": int(time.time()),
                    "type": "volume_boosting" if is_volume_boosting else "arbitrage",
                    "error": str(e),
                    "status": "failed"
                }
                
                # 尝试添加尽可能多的信息
                if 'token_in' in locals():
                    trade_record["token_in"] = token_in
                if 'token_out' in locals() or 'final_token_name' in locals():
                    trade_record["token_out"] = final_token_name if 'final_token_name' in locals() else token_out
                if 'buy_dex' in locals():
                    trade_record["buy_dex"] = buy_dex
                if 'sell_dex' in locals():
                    trade_record["sell_dex"] = sell_dex
                if 'amount_in' in locals():
                    trade_record["amount"] = amount_in
                
                self.trade_history.append(trade_record)
                
                # 更新统计信息
                self.stats["total_trades"] += 1
                self.stats["failed_trades"] += 1
            except Exception as inner_e:
                logger.error(f"记录失败交易时出错: {inner_e}")
            
            return False, {"error": str(e)}
    
    def save_trade_history(self):
        """保存交易历史到文件"""
        try:
            # 保存交易历史
            with open("data/trade_history.json", "w") as f:
                json.dump(self.trade_history, f, indent=2)
            
            # 更新统计数据
            # 计算平均利润
            if self.stats["successful_trades"] > 0:
                self.stats["average_profit"] = self.stats["total_profit"] / self.stats["successful_trades"]
            else:
                self.stats["average_profit"] = 0
                
            self.stats["last_updated"] = datetime.now().isoformat()
            
            # 保存统计摘要
            with open("data/trade_summary.json", "w") as f:
                json.dump(self.stats, f, indent=2)
                
            logger.info(f"已保存 {len(self.trade_history)} 条交易记录")
            
        except Exception as e:
            logger.error(f"保存交易历史失败: {e}")
    
    async def run(self):
        """运行套利机器人"""
        logger.info("开始运行MonadDEX套利机器人...")
        
        try:
            while True:
                # 查找并执行套利机会
                await self.find_and_execute_arbitrage()
                
                # 保存交易历史
                self.save_trade_history()
                
                # 等待下一轮检查
                interval = ARBITRAGE_CONFIG["price_check_interval"]
                logger.info(f"等待下一轮检查 ({interval}秒)...")
                await asyncio.sleep(interval)
                
        except KeyboardInterrupt:
            logger.info("套利机器人被用户中断")
        except Exception as e:
            logger.error(f"套利机器人运行出错: {e}", exc_info=True)
        finally:
            # 保存最终的交易记录
            self.save_trade_history()
            logger.info("套利机器人已停止")

async def main():
    """主函数"""
    try:
        # 创建并运行套利机器人
        bot = ArbitrageBot()
        await bot.run()
    except Exception as e:
        logger.error(f"主程序运行错误: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main()) 