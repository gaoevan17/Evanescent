# 打包清单（PACKING LIST）

提交物：`共识护航_AI_Banking_Agent_源码与部署材料.zip`

```
consensus_guardian/
├── README.md                    # 安装/配置/启动/登录/复现说明
├── requirements.txt             # 依赖（宽松版本范围）
├── requirements.lock.txt        # 锁定版本
├── .env.example                 # 配置示例（无真实密钥）
├── run.py                       # 启动入口
├── requirements_matrix.md       # 需求追踪矩阵（合规基线）
├── app/                         # 后端源码
│   ├── main.py  config.py  db.py
│   ├── models/  security/  auth/  services/  tools/  llm/  adapters/
├── static/                      # Web 前端（index.html/app.js/style.css）
├── tests/                       # 测试用例（128 条）
├── scripts/init_db.py           # 初始化 + 合成演示数据 + 重置入口
├── docs/
│   ├── 技术文档.md              # 技术文档可编辑源文件
│   ├── 安全自评报告.md          # 独立安全自评
│   ├── API文档.md
│   ├── 作品简介.txt             # 200 字内简介
│   ├── 第三方依赖与许可证.md
│   ├── 演示脚本.md              # 5 分钟视频脚本（待录制）
│   ├── 答辩PPT大纲.md           # 10 分钟答辩大纲
│   └── PACKING_LIST.md          # 本清单
└── data/                        # 运行时生成（打包时清空，不含真实数据）
```

## 打包排除项（已排除）

- `.env`（真实密钥）、`__pycache__`、`.venv`、`.pytest_cache`
- `data/*.db`（运行生成，含模拟数据但不含真实数据；打包时清空，README 说明如何重建）
- 大型模型权重、依赖缓存

## 校验信息

打包后解压到新临时目录，按 README 启动并验证关键流程后记录：
- 文件大小、SHA256 校验值（打包脚本输出）。
