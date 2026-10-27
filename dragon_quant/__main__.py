"""python -m dragon_quant → 打印 CLI 帮助（不带子命令不会执行扫描）"""
from dragon_quant.cli import main

if __name__ == "__main__":
    main()
