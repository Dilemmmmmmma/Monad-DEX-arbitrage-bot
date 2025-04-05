import os
import time
import json
import logging
import decimal
import sys
import asyncio
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

from config import DEX_ROUTERS, DEX_TYPES, TOKENS, ARBITRAGE_CONFIG, WRAPPED_MON, VOLUME_BOOSTING
from utils.helpers import get_web3, load_abi, format_amount, format_address, get_deadline

# 设置日志记录 - 修改为只输出到控制台，不记录文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # 移除文件日志处理器
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("transaction_executor")

# 确保日志目录存在
os.makedirs("logs", exist_ok=True)

class TransactionExecutor:
    def __init__(self):
        """初始化交易执行器"""
        # 加载环境变量
        load_dotenv()
        
        # 初始化Web3连接
        self.web3 = get_web3()
        
        # 获取账户信息
        self.account = self.web3.eth.account.from_key(os.getenv('PRIVATE_KEY'))
        self.address = self.account.address
        
        # 加载ABI
        self.router_abi = load_abi("contracts/router_abi.json")
        self.algebra_router_abi = load_abi("contracts/algebra_router_abi.json")
        self.erc20_abi = load_abi("contracts/erc20_abi.json")
        
        # 加载特定DEX的ABI（如果有的话）
        self.specific_router_abis = {}
        for dex_name in DEX_ROUTERS.keys():
            specific_abi_path = f"contracts/{dex_name}_router_abi.json"
            try:
                if os.path.exists(specific_abi_path):
                    self.specific_router_abis[dex_name] = load_abi(specific_abi_path)
            except Exception as e:
                logger.error(f"加载特定DEX {dex_name} ABI失败: {e}")
        
        # 初始化DEX路由器合约
        self.routers = {}
        for dex_name, router_address in DEX_ROUTERS.items():
            try:
                router_address = Web3.to_checksum_address(router_address)
                
                # 选择正确的ABI：优先使用特定DEX的ABI，如果没有则根据DEX类型选择
                if dex_name in self.specific_router_abis:
                    abi = self.specific_router_abis[dex_name]
                elif dex_name in DEX_TYPES and DEX_TYPES[dex_name] == "algebra":
                    abi = self.algebra_router_abi
                else:
                    abi = self.router_abi
                    
                self.routers[dex_name] = self.web3.eth.contract(
                    address=router_address,
                    abi=abi
                )
                
            except Exception as e:
                logger.error(f"加载 {dex_name} 路由器失败: {e}")
        
        # 初始化代币相关
        self.tokens = {}
        self.token_decimals = {}
        self._weth_addresses = {}  # 缓存WETH地址
        self._pool_deployers = {}  # 缓存pool deployer地址(针对Algebra DEX)
        
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
        
        # 为每个代币创建合约实例
        for token_name, token_address in TOKENS.items():
            if token_name == "MON" or not token_address:
                continue
                
            try:
                token_address = Web3.to_checksum_address(token_address)
                token_contract = self.web3.eth.contract(
                    address=token_address,
                    abi=self.erc20_abi
                )
                self.tokens[token_name] = token_contract
            except Exception as e:
                logger.error(f"创建代币 {token_name} 合约实例失败: {e}")
                

        
        self.pre_tx_mon_balance = 0  # 交易前的MON余额
    
    async def get_weth_address(self, dex_name):
        """获取指定DEX的WETH地址（即包装后的MON地址）"""
        # 检查缓存
        if dex_name in self._weth_addresses:
            return self._weth_addresses[dex_name]
            
        # 如果配置中有WRAPPED_MON，直接使用
        if WRAPPED_MON:
 
            self._weth_addresses[dex_name] = Web3.to_checksum_address(WRAPPED_MON)
            return self._weth_addresses[dex_name]
            
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
    
    async def get_pool_deployer(self, dex_name):
        """获取指定Algebra DEX的pool deployer地址"""
        # 检查缓存
        if dex_name in self._pool_deployers:
            return self._pool_deployers[dex_name]
            
        # 尝试从路由器获取
        if dex_name in self.routers and DEX_TYPES.get(dex_name) == "algebra":
            try:
                router = self.routers[dex_name]
                pool_deployer = router.functions.poolDeployer().call()
                self._pool_deployers[dex_name] = pool_deployer
                logger.info(f"获取 {dex_name} pool deployer成功: {pool_deployer}")
                return pool_deployer
            except Exception as e:
                logger.error(f"获取 {dex_name} pool deployer失败: {e}")
                # 返回零地址作为fallback
                return "0x0000000000000000000000000000000000000000"
                
        # 如果不是Algebra DEX，返回零地址
        return "0x0000000000000000000000000000000000000000"
    
    async def get_native_balance(self):
        """获取账户原生代币(MON)余额"""
        try:
            balance_wei = self.web3.eth.get_balance(self.address)
            balance = self.web3.from_wei(balance_wei, 'ether')
            return float(balance)
        except Exception as e:
            logger.error(f"获取MON余额失败: {e}")
            return 0
    
    async def get_token_balance(self, token_name):
        """获取账户指定代币余额"""
        try:
            # 如果是原生代币MON，直接获取余额
            if token_name == "MON":
                return await self.get_native_balance()
                
            # 检查代币合约是否存在
            if token_name not in self.tokens:
                logger.error(f"代币 {token_name} 未加载")
                return 0
                
            # 获取代币余额
            token_contract = self.tokens[token_name]
            balance_wei = token_contract.functions.balanceOf(self.address).call()
            
            # 获取代币精度
            decimals = await self.get_token_decimals(token_name)
            
            # 计算实际余额
            balance = balance_wei / (10 ** decimals)
            
            return balance
            
        except Exception as e:
            logger.error(f"获取代币 {token_name} 余额失败: {e}")
            return 0
    
    async def get_token_decimals(self, token_name):
        """获取代币精度"""
        # 如果已经缓存了精度，直接返回
        if token_name in self.token_decimals:
            return self.token_decimals[token_name]
            
        try:
            # 如果是原生代币MON，返回默认精度
            if token_name == "MON":
                return ARBITRAGE_CONFIG["mon_decimals"]
                
            # 如果代币合约未加载，尝试获取默认精度
            if token_name not in self.tokens:
                if token_name in ["USDC", "USDT"]:
                    return 6
                else:
                    return 18
            
            # 从合约获取精度
            token_contract = self.tokens[token_name]
            decimals = token_contract.functions.decimals().call()
            
            # 缓存精度
            self.token_decimals[token_name] = decimals
            
            return decimals
            
        except Exception as e:
            logger.error(f"获取代币 {token_name} 精度失败: {e}")
            # 返回默认精度
            if token_name in ["USDC", "USDT"]:
                return 6
            else:
                return 18
    
    async def approve_token(self, token_name, router_address, amount=None):
        """
        为DEX路由器批准代币使用权
        
        参数:
            token_name (str): 代币名称
            router_address (str): 路由器地址
            amount (int, optional): 批准金额，默认为最大值
            
        返回:
            dict: 交易结果
        """
        try:
            # 检查是否为原生代币
            if token_name == "MON":
                # 原生代币不需要批准
                return {"success": True, "tx_hash": None}
            
            # 检查代币合约是否存在
            if token_name not in self.tokens:
                logger.error(f"代币 {token_name} 未加载")
                return {"success": False, "error": f"代币 {token_name} 未加载"}
            
            token_contract = self.tokens[token_name]
            
            # 设置批准金额为最大值（无限批准）
            max_amount = 2**256 - 1  # MAX_UINT256
            
            # 检查当前批准额度
            current_allowance = token_contract.functions.allowance(
                self.address, router_address
            ).call()
            
            # 如果批准额度大于0，则视为已批准，不再重复批准
            if current_allowance > 0:

                return {"success": True, "tx_hash": None}
            
            # 获取gas限制
            gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 200000)
                
            # 构建批准交易（始终使用无限批准）
            transaction = token_contract.functions.approve(
                router_address, max_amount
            ).build_transaction({
                'from': self.address,
                'gas': gas_limit,
                'maxFeePerGas': self.web3.eth.gas_price,
                'nonce': self.web3.eth.get_transaction_count(self.address),
            })
            
            # 签名交易
            signed_tx = self.web3.eth.account.sign_transaction(transaction, self.account.key)
            
            # 发送交易
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()
            logger.info(f"批准代币 {token_name} 无限使用权，交易哈希: {tx_hash_hex}")
            
            # 等待交易确认
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            # 检查交易状态
            if receipt['status'] == 1:
                logger.info(f"批准代币 {token_name} 无限使用权成功")
                return {"success": True, "tx_hash": tx_hash_hex}
            else:
                logger.error(f"批准代币 {token_name} 使用权失败")
                return {"success": False, "error": "批准代币使用权交易执行失败", "tx_hash": tx_hash_hex}
                
        except Exception as e:
            logger.error(f"批准代币 {token_name} 使用权失败: {e}")
            return {"success": False, "error": str(e)}
    
    async def execute_swap(self, dex_name, token_in, token_out, amount, slippage_percent=1.0):
        """
        执行代币兑换操作
        
        参数:
            dex_name (str): DEX名称
            token_in (str): 输入代币名称
            token_out (str): 输出代币名称
            amount (float): 输入金额（考虑精度）
            slippage_percent (float): 滑点容忍百分比
            
        返回:
            dict: 交易结果
        """
        start_time = time.time()
        try:
            # 检查路由器是否存在
            if dex_name not in self.routers:
                logger.error(f"DEX {dex_name} 未加载")
                return {"success": False, "error": f"DEX {dex_name} 未加载"}
            
            router = self.routers[dex_name]
            router_address = router.address
            
            # 获取WETH地址（用于原生代币MON）
            weth_address = await self.get_weth_address(dex_name)
            if not weth_address and (token_in == "MON" or token_out == "MON"):
                return {"success": False, "error": f"无法获取 {dex_name} 的WETH地址"}
            
            # 确定token_in地址和精度
            if token_in == "MON":
                token_in_address = weth_address
                token_in_decimals = ARBITRAGE_CONFIG['mon_decimals']
            else:
                if token_in not in TOKENS or not TOKENS[token_in]:
                    return {"success": False, "error": f"代币 {token_in} 地址未配置"}
                token_in_address = Web3.to_checksum_address(TOKENS[token_in])
                token_in_decimals = await self.get_token_decimals(token_in)
            
            # 确定token_out地址和精度
            if token_out == "MON":
                token_out_address = weth_address
                token_out_decimals = ARBITRAGE_CONFIG['mon_decimals']
            else:
                if token_out not in TOKENS or not TOKENS[token_out]:
                    return {"success": False, "error": f"代币 {token_out} 地址未配置"}
                token_out_address = Web3.to_checksum_address(TOKENS[token_out])
                token_out_decimals = await self.get_token_decimals(token_out)
            
            # 检查余额
            current_balance = await self.get_token_balance(token_in)
            if float(current_balance) < float(amount):
                logger.error(f"{token_in} 余额不足: 当前 {current_balance}, 需要 {amount}")
                return {"success": False, "error": f"{token_in} 余额不足"}
            
            # 转换为代币精度
            amount_in_wei = int(float(amount) * (10 ** token_in_decimals))
            
            # 确定DEX类型
            dex_type = DEX_TYPES.get(dex_name, "uniswap_v2")
            
            if dex_type == "algebra":
                # 为Algebra DEX执行交易
                return await self._execute_algebra_swap(
                    dex_name, router, token_in, token_out, token_in_address, token_out_address, 
                    amount, amount_in_wei, token_in_decimals, token_out_decimals, slippage_percent
                )
            else:
                # 为UniswapV2 DEX执行交易
                return await self._execute_uniswap_v2_swap(
                    dex_name, router, token_in, token_out, token_in_address, token_out_address, 
                    amount, amount_in_wei, token_in_decimals, token_out_decimals, slippage_percent
                )
                
        except Exception as e:
            logger.error(f"执行交易时出错: {e}")
            return {"success": False, "error": str(e)}
    
    async def _execute_uniswap_v2_swap(self, dex_name, router, token_in, token_out, token_in_address, token_out_address, 
                                      amount, amount_in_wei, token_in_decimals, token_out_decimals, slippage_percent):
        """为UniswapV2 DEX执行交易"""
        start_time = time.time()  # 添加开始时间记录
        try:
            # 计算预期输出金额
            path = [token_in_address, token_out_address]
            try:
                amounts_out = router.functions.getAmountsOut(amount_in_wei, path).call()
                min_amount_out = int(amounts_out[1] * (1 - slippage_percent / 100))
            except Exception as e:
                logger.error(f"计算预期输出金额失败: {e}")
                return {"success": False, "error": f"计算预期输出金额失败: {str(e)}"}
            
            # 为非原生代币批准路由器使用
            if token_in != "MON":
                approve_result = await self.approve_token(token_in, router.address, amount)
                if not approve_result["success"]:
                    return approve_result
            
            # 设置交易截止时间
            deadline = int(time.time() + 300)
            
            # 获取gas限制
            gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 200000)
            
            # 为原生代币和代币交易使用不同的方法
            if token_in == "MON" and token_out != "MON":
                # MON -> Token (使用 swapExactETHForTokens)
                logger.info(f"兑换 {amount} MON -> {token_out} 在 {dex_name}")
                
                transaction = router.functions.swapExactETHForTokens(
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'value': amount_in_wei,
                    'gas': gas_limit,
                    'maxFeePerGas': self.web3.eth.gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
                
            elif token_in != "MON" and token_out == "MON":
                # Token -> MON (使用 swapExactTokensForETH)
                logger.info(f"兑换 {amount} {token_in} -> MON 在 {dex_name}")
                
                transaction = router.functions.swapExactTokensForETH(
                    amount_in_wei,
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'gas': gas_limit,
                    'maxFeePerGas': self.web3.eth.gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
                
            else:
                # Token -> Token (使用 swapExactTokensForTokens)
                logger.info(f"兑换 {amount} {token_in} -> {token_out} 在 {dex_name}")
                
                transaction = router.functions.swapExactTokensForTokens(
                    amount_in_wei,
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'gas': gas_limit,
                    'maxFeePerGas': self.web3.eth.gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
            
            # 签名交易
            signed_tx = self.web3.eth.account.sign_transaction(transaction, self.account.key)
            
            # 发送交易
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()

            
            # 等待交易确认
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            # 计算交易耗时
            execution_time = time.time() - start_time
            
            # 检查交易状态
            if receipt['status'] == 1:
                # 获取实际输出金额
                if token_out == "MON":
                    # 获取MON余额
                    initial_balance = self.pre_tx_mon_balance
                    final_balance = await self.get_native_balance()
                    token_out_amount = float(final_balance) - float(initial_balance)
                else:
                    # 获取ERC20代币余额
                    token_out_contract = self.tokens[token_out]
                    current_balance = token_out_contract.functions.balanceOf(self.address).call()
                    token_out_amount = current_balance / (10 ** token_out_decimals)
                
                # 保留基本输出日志，但简化
                logger.info(f"交易成功: {token_out_amount} {token_out}")
                return True, tx_hash_hex, token_out_amount
            else:
                logger.error(f"交易失败，交易哈希: {tx_hash_hex}")
                return False, tx_hash_hex, 0
                
        except ContractLogicError as e:
            error_msg = str(e)
            logger.error(f"合约执行错误: {error_msg}")
            
            if "insufficient funds" in error_msg.lower():
                return {"success": False, "error": f"{token_in} 余额不足"}
            elif "execution reverted" in error_msg.lower():
                return {"success": False, "error": f"执行回滚: {error_msg}"}
            else:
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            logger.error(f"执行UniswapV2交易时出错: {e}")
            return {"success": False, "error": str(e)}
    
    async def _execute_algebra_swap(self, dex_name, router, token_in, token_out, token_in_address, token_out_address, 
                                     amount, amount_in_wei, token_in_decimals, token_out_decimals, slippage_percent):
        """为Algebra DEX执行交易"""
        start_time = time.time()  # 添加开始时间记录
        try:
            # 获取pool deployer地址
            pool_deployer = await self.get_pool_deployer(dex_name)
            
            # 设置截止时间
            deadline = get_deadline(20)  # 20分钟后到期
            
            # 为非原生代币批准路由器使用
            if token_in != "MON":
                approve_result = await self.approve_token(token_in, router.address, amount)
                if not approve_result["success"]:
                    return approve_result
            
            # 获取gas限制
            gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 200000)
            
            # 计算预期输出和最小输出
            # 注意：Algebra不能像UniswapV2那样直接获取预期输出，我们需要模拟交易
            # 构建exactInputSingle参数
            params = {
                'tokenIn': token_in_address,
                'tokenOut': token_out_address,
                'deployer': pool_deployer,
                'recipient': self.address,
                'deadline': deadline,
                'amountIn': amount_in_wei,
                'amountOutMinimum': 0,  # 先设为0用于查询预期输出
                'limitSqrtPrice': 0  # 0表示不设置价格限制
            }
            
            try:
                # 使用call()模拟交易，获取预期输出量
                if token_in == "MON":
                    # 如果输入是MON，需要设置value
                    expected_out = router.functions.exactInputSingle(params).call({
                        'from': self.address,
                        'value': amount_in_wei
                    })
                else:
                    expected_out = router.functions.exactInputSingle(params).call()
                    
                # 计算最小输出（考虑滑点）
                min_amount_out = int(expected_out * (1 - slippage_percent / 100))
                logger.info(f"Algebra预期输出: {expected_out / (10 ** token_out_decimals)} {token_out}, 最小输出: {min_amount_out / (10 ** token_out_decimals)} {token_out}")
                
                # 更新amountOutMinimum
                params['amountOutMinimum'] = min_amount_out
                
            except Exception as e:
                logger.error(f"计算Algebra预期输出失败: {e}")
                return {"success": False, "error": f"计算预期输出失败: {str(e)}"}
            
            # 执行实际交易
            try:
                logger.info(f"Algebra兑换 {amount} {token_in} -> {token_out} 在 {dex_name}")
                
                # 构建交易
                if token_in == "MON":
                    # 如果输入是MON，需要设置value
                    transaction = router.functions.exactInputSingle(params).build_transaction({
                        'from': self.address,
                        'value': amount_in_wei,
                        'gas': gas_limit,
                        'maxFeePerGas': self.web3.eth.gas_price,
                        'nonce': self.web3.eth.get_transaction_count(self.address),
                    })
                else:
                    transaction = router.functions.exactInputSingle(params).build_transaction({
                        'from': self.address,
                        'gas': gas_limit,
                        'maxFeePerGas': self.web3.eth.gas_price,
                        'nonce': self.web3.eth.get_transaction_count(self.address),
                    })
                
                # 签名交易
                signed_tx = self.web3.eth.account.sign_transaction(transaction, self.account.key)
                
                # 发送交易
                tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
                tx_hash_hex = tx_hash.hex()

                
                # 等待交易确认
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                
                # 计算交易耗时
                execution_time = time.time() - start_time
                
                # 检查交易状态
                if receipt['status'] == 1:
                    # 获取实际输出金额
                    if token_out == "MON":
                        # 获取MON余额
                        initial_balance = self.pre_tx_mon_balance
                        final_balance = await self.get_native_balance()
                        token_out_amount = float(final_balance) - float(initial_balance)
                    else:
                        # 获取ERC20代币余额
                        token_out_contract = self.tokens[token_out]
                        current_balance = token_out_contract.functions.balanceOf(self.address).call()
                        token_out_amount = current_balance / (10 ** token_out_decimals)
                    
                    logger.info(f"Algebra交易成功，耗时: {execution_time:.2f}秒")
                    
                    return {
                        "success": True,
                        "tx_hash": tx_hash_hex,
                        "gas_used": receipt['gasUsed'],
                        "gas_cost": self.web3.from_wei(receipt['gasUsed'] * self.web3.eth.gas_price, 'ether'),
                        "amount_in": amount,
                        "amount_out": expected_out / (10 ** token_out_decimals),
                        "execution_time": execution_time
                    }
                else:
                    logger.error(f"Algebra交易失败，交易哈希: {tx_hash_hex}")
                    return {
                        "success": False,
                        "tx_hash": tx_hash_hex,
                        "error": "交易执行失败",
                        "gas_cost": self.web3.from_wei(receipt['gasUsed'] * self.web3.eth.gas_price, 'ether'),
                    }
                    
            except Exception as e:
                logger.error(f"执行Algebra交易失败: {e}")
                return {"success": False, "error": str(e)}
                
        except Exception as e:
            logger.error(f"执行Algebra交易时出错: {e}")
            return {"success": False, "error": str(e)}

    async def swap_tokens(self, dex_name, token_in, token_out, amount_in, min_amount_out=0, deadline=None, is_volume_boosting=False):
        """
        执行代币兑换操作的简化版本，专为arbitrage_bot设计
        
        Args:
            dex_name (str): DEX名称
            token_in (str): 输入代币名称
            token_out (str): 输出代币名称
            amount_in (int): 输入金额（以wei为单位）
            min_amount_out (int, optional): 最小输出金额（以wei为单位），默认为0
            deadline (int, optional): 交易截止时间戳，默认为当前时间+5分钟
            is_volume_boosting (bool, optional): 是否为刷交易量模式，用于选择gas_limit
            
        Returns:
            tuple: (success, tx_hash, amount_out)
            - success (bool): 交易是否成功
            - tx_hash (str): 交易哈希
            - amount_out (float): 获得的代币数量（已考虑精度）
        """
        try:
            if dex_name not in self.routers:
                logger.error(f"DEX {dex_name} 不存在")
                return False, None, 0
                
            router = self.routers[dex_name]
            router_address = router.address
            
            # 设置默认deadline
            if deadline is None:
                deadline = int(time.time() + 300)  # 5分钟过期
            
            # 获取WETH地址（用于原生代币MON）
            weth_address = await self.get_weth_address(dex_name)
            if not weth_address and (token_in == "MON" or token_out == "MON"):
                logger.error(f"无法获取 {dex_name} 的WETH地址")
                return False, None, 0
            
            # 如果输出代币是MON，保存交易前的余额用于计算交易后获得的数量
            if token_out == "MON":
                self.pre_tx_mon_balance = await self.get_native_balance()
                logger.info(f"交易前MON余额: {self.pre_tx_mon_balance}")
            
            # 确定token_in地址和精度
            if token_in == "MON":
                token_in_address = weth_address
                token_in_decimals = ARBITRAGE_CONFIG['mon_decimals']
                # 检查原生代币余额
                amount_in_eth = self.web3.from_wei(amount_in, 'ether')
                current_balance = await self.get_native_balance()
                if float(current_balance) < float(amount_in_eth):
                    logger.error(f"MON余额不足: 当前 {current_balance}, 需要 {amount_in_eth}")
                    return False, None, 0
            else:
                if token_in not in TOKENS or not TOKENS[token_in]:
                    logger.error(f"代币 {token_in} 地址未配置")
                    return False, None, 0
                token_in_address = Web3.to_checksum_address(TOKENS[token_in])
                token_in_decimals = await self.get_token_decimals(token_in)
                # 检查代币余额
                current_balance_wei = self.tokens[token_in].functions.balanceOf(self.address).call()
                if current_balance_wei < amount_in:
                    current_balance = current_balance_wei / (10 ** token_in_decimals)
                    amount_in_human = amount_in / (10 ** token_in_decimals)
                    logger.error(f"{token_in}余额不足: 当前 {current_balance}, 需要 {amount_in_human}")
                    return False, None, 0
            
            # 确定token_out地址和精度
            if token_out == "MON":
                token_out_address = weth_address
                token_out_decimals = ARBITRAGE_CONFIG['mon_decimals']
            else:
                if token_out not in TOKENS or not TOKENS[token_out]:
                    logger.error(f"代币 {token_out} 地址未配置")
                    return False, None, 0
                token_out_address = Web3.to_checksum_address(TOKENS[token_out])
                token_out_decimals = await self.get_token_decimals(token_out)
            
            # 为非原生代币批准路由器使用
            if token_in != "MON":
                # 计算批准金额（以人类可读形式）
                amount_to_approve = amount_in / (10 ** token_in_decimals)
                approve_result = await self.approve_token(token_in, router.address, amount_to_approve)
                if not approve_result["success"]:
                    logger.error(f"批准{token_in}代币失败")
                    return False, None, 0
            
            # 确定DEX类型并执行交易
            dex_type = DEX_TYPES.get(dex_name, "uniswap_v2")
            
            if dex_type == "algebra":
                return await self._swap_tokens_algebra(dex_name, router, token_in, token_out, token_in_address, token_out_address, amount_in, min_amount_out, token_out_decimals, deadline, is_volume_boosting)
            else:
                return await self._swap_tokens_uniswap_v2(dex_name, router, token_in, token_out, token_in_address, token_out_address, amount_in, min_amount_out, token_out_decimals, deadline, is_volume_boosting)
                
        except Exception as e:
            logger.error(f"swap_tokens函数出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, 0
    
    async def _swap_tokens_uniswap_v2(self, dex_name, router, token_in, token_out, token_in_address, token_out_address, amount_in, min_amount_out, token_out_decimals, deadline, is_volume_boosting=False):
        """为UniswapV2类型DEX执行交易"""
        try:
            # 记录交易开始时间
            start_time = time.time()
            
            # 创建交易路径
            path = [token_in_address, token_out_address]
            
            # 如果是原生代币交易，先记录初始余额
            if token_out == "MON":
                self.pre_tx_mon_balance = await self.get_native_balance()
            
            # 根据交易模式选择gas_limit和gas_price_multiplier
            if is_volume_boosting:
                # 使用刷交易量模式的gas设置
                gas_limit = VOLUME_BOOSTING.get("gas_limit", 200000)
                gas_price_multiplier = VOLUME_BOOSTING.get("gas_price_multiplier", 1.15)
            else:
                # 使用常规套利模式的gas设置
                gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 170000)
                gas_price_multiplier = ARBITRAGE_CONFIG.get("gas_price_multiplier", 1.1)
            
            # 获取当前gas价格并应用乘数
            gas_price = int(self.web3.eth.gas_price * gas_price_multiplier)
            
            # 尝试获取预期输出
            try:
                amounts_out = router.functions.getAmountsOut(amount_in, path).call()
                expected_out = amounts_out[1]
                
                # 如果未指定最小输出，根据滑点计算
                if min_amount_out == 0:
                    # 计算滑点
                    slippage = ARBITRAGE_CONFIG.get("slippage_tolerance", 1.0) / 100  # 转换为小数
                    min_amount_out = int(expected_out * (1 - slippage))
                
                # 保留交易详情日志
                amount_in_readable = amount_in / (10 ** self.token_decimals.get(token_in, 18))
                expected_out_readable = expected_out / (10 ** token_out_decimals)
                min_out_readable = min_amount_out / (10 ** token_out_decimals)
            except Exception as e:
                logger.warning(f"计算预期输出失败，使用0作为最小输出: {e}")
                # 继续使用0作为最小输出
            
            # 为原生代币和代币交易使用不同的方法
            if token_in == "MON" and token_out != "MON":
                # MON -> Token (使用 swapExactETHForTokens)
                # 简化日志输出
                # 保留必要的交易信息
                logger.info(f"兑换 MON -> {token_out} 在 {dex_name}")
                
                transaction = router.functions.swapExactETHForTokens(
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'value': amount_in,
                    'gas': gas_limit,
                    'maxFeePerGas': gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
                
            elif token_in != "MON" and token_out == "MON":
                # Token -> MON (使用 swapExactTokensForETH)
                # 简化日志输出
                logger.info(f"兑换 {token_in} -> MON 在 {dex_name}")
                
                transaction = router.functions.swapExactTokensForETH(
                    amount_in,
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'gas': gas_limit,
                    'maxFeePerGas': gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
                
            else:
                # Token -> Token (使用 swapExactTokensForTokens)
                # 简化日志输出
                logger.info(f"兑换 {token_in} -> {token_out} 在 {dex_name}")
                
                transaction = router.functions.swapExactTokensForTokens(
                    amount_in,
                    min_amount_out,
                    path,
                    self.address,
                    deadline
                ).build_transaction({
                    'from': self.address,
                    'gas': gas_limit,
                    'maxFeePerGas': gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
            
            # 签名交易
            signed_tx = self.web3.eth.account.sign_transaction(transaction, self.account.key)
            
            # 发送交易
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()
            
            # 等待交易确认
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            # 计算交易耗时
            execution_time = time.time() - start_time
            
            # 检查交易状态
            if receipt['status'] == 1:
                # 交易成功
                # 简化日志输出
                
                # 获取实际输出金额
                if token_out == "MON":
                    # 获取MON余额
                    initial_balance = self.pre_tx_mon_balance
                    final_balance = await self.get_native_balance()
                    token_out_amount = float(final_balance) - float(initial_balance)
                else:
                    # 获取ERC20代币余额
                    token_out_contract = self.tokens[token_out]
                    current_balance = token_out_contract.functions.balanceOf(self.address).call()
                    token_out_amount = current_balance / (10 ** token_out_decimals)
                
                # 保留基本输出日志，但简化
                logger.info(f"交易成功: {token_out_amount} {token_out}")
                return True, tx_hash_hex, token_out_amount
            else:
                # 交易失败
                logger.error(f"交易失败，交易哈希: {tx_hash_hex}")
                return False, tx_hash_hex, 0
                
        except Exception as e:
            logger.error(f"执行UniswapV2交易时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, 0
            
    async def _swap_tokens_algebra(self, dex_name, router, token_in, token_out, token_in_address, token_out_address, amount_in, min_amount_out, token_out_decimals, deadline, is_volume_boosting=False):
        """为Algebra DEX执行代币交换"""
        try:
            # 获取pool deployer地址
            pool_deployer = await self.get_pool_deployer(dex_name)
            
            # 如果没有设置最小输出金额，尝试计算预期输出并设置滑点
            params = {
                'tokenIn': token_in_address,
                'tokenOut': token_out_address,
                'deployer': pool_deployer,
                'recipient': self.address,
                'deadline': deadline,
                'amountIn': amount_in,
                'amountOutMinimum': min_amount_out,
                'limitSqrtPrice': 0  # 0表示不设置价格限制
            }
            
            # 如果最小输出为0，尝试计算预期输出
            if min_amount_out == 0:
                try:
                    # 获取预期输出（模拟交易）
                    if token_in == "MON":
                        expected_out = router.functions.exactInputSingle(params).call({
                            'from': self.address,
                            'value': amount_in
                        })
                    else:
                        expected_out = router.functions.exactInputSingle(params).call()
                    
                    # 设置1%滑点
                    min_amount_out = int(expected_out * 0.99)
                    params['amountOutMinimum'] = min_amount_out
                    logger.info(f"计算预期输出: {expected_out / (10 ** token_out_decimals)} {token_out}, 最小输出: {min_amount_out / (10 ** token_out_decimals)} {token_out}")
                except Exception as e:
                    logger.warning(f"计算预期输出失败，使用0作为最小输出: {e}")
                    # 继续使用0作为最小输出
            
            # 根据交易模式选择gas_limit
            if is_volume_boosting:
                # 使用刷交易量模式的gas_limit
                gas_limit = VOLUME_BOOSTING.get("gas_limit", 170000)
                logger.info(f"使用刷交易量模式gas限制: {gas_limit}")
            else:
                # 使用常规套利模式的gas_limit
                gas_limit = ARBITRAGE_CONFIG.get("gas_limit", 200000)
                logger.info(f"使用常规套利模式gas限制: {gas_limit}")
            
            # 构建交易
            logger.info(f"执行Algebra交易: {token_in} -> {token_out}")
            
            if token_in == "MON":
                transaction = router.functions.exactInputSingle(params).build_transaction({
                    'from': self.address,
                    'value': amount_in,
                    'gas': gas_limit,
                    'maxFeePerGas': self.web3.eth.gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
            else:
                transaction = router.functions.exactInputSingle(params).build_transaction({
                    'from': self.address,
                    'gas': gas_limit,
                    'maxFeePerGas': self.web3.eth.gas_price,
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                })
            
            # 签名并发送交易
            signed_tx = self.web3.eth.account.sign_transaction(transaction, self.account.key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = tx_hash.hex()

            
            # 等待交易确认
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            
            # 检查交易状态
            if receipt['status'] == 1:
                # 交易成功
                logger.info(f"交易成功")
                
                # 获取实际输出金额
                if token_out == "MON":
                    # 获取MON余额
                    initial_balance = self.pre_tx_mon_balance
                    final_balance = await self.get_native_balance()
                    token_out_amount = float(final_balance) - float(initial_balance)
                else:
                    # 获取ERC20代币余额
                    token_out_contract = self.tokens[token_out]
                    current_balance = token_out_contract.functions.balanceOf(self.address).call()
                    token_out_amount = current_balance / (10 ** token_out_decimals)
                
                logger.info(f"获得 {token_out_amount} {token_out}")
                return True, tx_hash_hex, token_out_amount
            else:
                # 交易失败
                logger.error(f"交易失败，交易哈希: {tx_hash_hex}")
                return False, tx_hash_hex, 0
                
        except Exception as e:
            logger.error(f"执行Algebra交易时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, 0

async def main():
    """测试交易执行功能"""
    executor = TransactionExecutor()
    
    # 测试获取余额
    mon_balance = await executor.get_native_balance()
    logger.info(f"MON 余额: {mon_balance}")
    
    # 测试获取代币余额
    for token_name in TOKENS:
        if token_name == "MON":
            continue
        balance = await executor.get_token_balance(token_name)
        logger.info(f"{token_name} 余额: {balance}")

if __name__ == "__main__":
    asyncio.run(main()) 