#!/usr/bin/env python3
"""V0.1 任务入口（薄封装）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vskill.phase2.v01 import main
raise SystemExit(main())
