# DocFusion-AI
服创赛-A23，大语言模型驱动的文档理解与多源数据融合系统。

当前主干目录已经统一为稳定英文命名，运行时数据与历史实验代码也做了分层，便于继续开发和维护。

## 目录结构

```text
backend/                 FastAPI 后端
backend/app/             后端源码
backend/storage/         运行时产物（SQLite、日志、上传文件、测试报告）
frontend/                当前前端静态页面
docs/                    项目文档与开发笔记
demo/screenshots/        演示截图
test_data/               测试样例与历史归档
legacy/                  历史实验代码与旧版本快照
start.py                 统一启动入口
```

## 命名约定

- 顶层目录统一使用英文语义名，避免空格、版本号和临时标记混入正式路径。
- 代码文件优先使用 `snake_case`，接口层只放路由，业务实现尽量收敛到 `backend/app/services/`。
- 运行时生成文件统一放到 `backend/storage/`，不要和源码目录混放。
- 测试样例统一放到 `test_data/`，历史实验或备份统一放到 `legacy/`。

## 启动

统一入口已经收敛到根目录 `start.py`，前端静态页面由 FastAPI 直接托管。

```bash
cd /Users/st.peter/Applications/pycharm/DocFusion-AI
conda activate 你的环境名
python start.py
```

打开：

```text
http://127.0.0.1:8000
```
