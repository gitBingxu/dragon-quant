#!/usr/bin/env python3
"""CLI 入口 — 委托给 dragon_quant 包"""
import sys
import os

# 确保包导入可用
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

from dragon_quant.main import main

if __name__ == "__main__":
    main()
