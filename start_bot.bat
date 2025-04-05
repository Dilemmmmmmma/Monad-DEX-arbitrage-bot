@echo off
color 0A
title MonadDEX套利机器人启动器

echo ================================================
echo           MonadDEX套利机器人启动器
echo ================================================
echo.

REM 检查虚拟环境是否存在
if not exist .venv (
    echo 虚拟环境不存在，正在创建...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建虚拟环境失败，请确认已安装Python。
        goto error
    )
    echo 虚拟环境创建成功！
) else (
    echo 检测到已有虚拟环境，直接使用。
)

echo.
echo 正在激活虚拟环境...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo 激活虚拟环境失败！
    goto error
)
echo 虚拟环境激活成功！

echo.
echo 正在安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo 安装依赖失败，请检查requirements.txt文件内容。
    goto error
)
echo 依赖安装完成！

echo.
echo 显示当前配置参数：
python -c "from config import ARBITRAGE_CONFIG, VOLUME_BOOSTING; print('套利配置:'); print(f'  最小利润阈值: {ARBITRAGE_CONFIG[\"min_profit_threshold\"]} MON'); print(f'  最大交易金额: {ARBITRAGE_CONFIG[\"max_trade_amount\"]} MON'); print(f'  价格差异阈值: {ARBITRAGE_CONFIG[\"price_diff_threshold\"]}%'); print(f'  交易间隔: {ARBITRAGE_CONFIG[\"trade_interval\"]}秒'); print(); print('刷交易量模式:'); print(f'  当前状态: {\"已启用\" if VOLUME_BOOSTING.get(\"enabled\", False) else \"已禁用\"}'); if VOLUME_BOOSTING.get(\"enabled\", False): print(f'  目标DEX: {VOLUME_BOOSTING.get(\"target_dex\", \"N/A\")}'); print(f'  亏损容忍度: {VOLUME_BOOSTING.get(\"loss_tolerance\", 0)}%'); print(f'  交易金额范围: {VOLUME_BOOSTING.get(\"min_trade_amount\", 0)}-{VOLUME_BOOSTING.get(\"max_trade_amount\", 0)} MON')"

echo.
echo 开始运行套利机器人...
echo 模式：实时监控，非测试模式。
echo 按Ctrl+C可随时终止运行。
echo.
echo ============== 运行日志开始 ==============
echo.

python arbitrage_bot.py
if errorlevel 1 (
    echo 执行过程中出错！
    goto error
)
goto end

:error
echo.
echo 执行过程中发生错误！
echo 按任意键退出...
pause >nul
exit /b 1

:end
call .venv\Scripts\deactivate.bat
echo.
echo 程序已结束，按任意键退出...
pause >nul
exit /b 0 