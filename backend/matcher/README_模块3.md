# 模块3对接与使用说明：语义字段匹配与规则校验

**提交人：** 负责人3  
**当前状态：** 代码已提交 PR，待代码审查 (Code Review)  

## 1. 模块概述
本模块已完成“语义字段匹配”与“规则校验”的核心功能开发。为规范后续联调开发与本地测试流程，特编写此说明文档。

## 2. 接口调用说明 (面向负责人4)
本模块对外提供统一的封装接口 `process_data`。调用方无需关心底层匹配与校验逻辑，直接调用该接口即可一键完成完整的数据处理流程。

```python
# 调用示例
from your_module import process_data 

# 传入输入数据，获取处理结果
result = process_data(input_data)
```
*(注：请根据实际的项目目录结构调整 `from your_module` 的导入路径)*

## 3. 本地环境配置与测试

### 3.1 安装依赖环境
在本地运行或测试前，请确保 Python 环境已就绪，并在终端执行以下命令安装相关依赖库：
```bash
pip install sentence-transformers scikit-learn numpy
```

### 3.2 运行测试脚本
依赖安装完成后，执行以下命令启动测试：
```bash
python test_matcher.py
```

> **注意事项：模型缓存机制**
> 首次运行测试时，程序会自动从远端下载语义向量模型（`text2vec-base-chinese`，文件体积约 400MB）。下载完成后将自动缓存至本地系统，后续启动将直接读取本地缓存，不会产生重复下载的开销。

### 3.3 网络加速配置 (可选)
若在拉取模型阶段遇到网络限速或请求超时等异常情况，建议通过配置环境变量的方式，将 HuggingFace 切换至国内镜像源。

**Windows CMD / PowerShell 环境：**
```cmd
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1
```

**Linux / macOS 环境：**
```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
```
*(环境变量配置完成后，再次执行 `python test_matcher.py` 即可生效)*