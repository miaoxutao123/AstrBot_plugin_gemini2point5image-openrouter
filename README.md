# AstrBot Gemini 2.5 图像生成插件

基于 OpenRouter API 的 AstrBot 图像生成插件，使用 Google Gemini 2.5 Flash 模型免费生成高质量图像。

## 功能特点

- 🎨 **免费图像生成**: 使用 OpenRouter 的免费 Gemini 2.5 Flash 模型
- 🔧 **自定义模型支持**: 可配置使用任何 OpenRouter 平台支持的图像生成模型
- ️ **参考图片支持**: 支持基于用户提供的图片进行生成或修改
- 🔑 **多API密钥支持**: 支持配置多个API密钥，自动轮换避免额度耗尽
- ♻️ **智能重试机制**: 支持可配置的自动重试，提高请求成功率和稳定性
- 🚀 **异步处理**: 基于 asyncio 的高性能异步图像生成
- 🔗 **智能文件传输**: 支持本地和远程服务器的文件传输
- 🧹 **自动清理**: 自动清理超过15分钟的历史图像文件
- 🛡️ **错误处理**: 完善的异常处理和错误提示
- 🌐 **多语言支持**: 自动将中文提示词翻译为英文

## 安装配置

### 1. 获取 API Key

前往 [OpenRouter](https://openrouter.ai/) 注册账号并获取 API Key。

### 2. 配置参数

#### 通过Web界面配置（推荐）

1. 访问AstrBot的Web管理界面
2. 进入"插件管理"页面
3. 找到"Gemini 2.5 Image OpenRouter"插件  
4. 点击"配置"按钮进行可视化配置

#### 配置参数说明

- **openrouter_api_keys**: OpenRouter API 密钥列表（支持多个密钥自动轮换）
- **model_name**: 使用的模型名称（默认：google/gemini-2.5-flash-image-preview:free）
- **max_retry_attempts**: 每个API密钥的最大重试次数（默认：3次，推荐2-5次）
- **custom_api_base**: 自定义 API Base URL（可选，没有特殊需求别填）
- **nap_server_address**: NAP cat 服务地址（同服务器填写 `localhost`）
- **nap_server_port**: 文件传输端口（默认 3658）

## 使用方法

### LLM 工具调用

插件注册了一个名为 `gemini-pic-gen` 的 LLM 工具，支持以下参数：

- `image_description`: 图像生成或修改描述（必需）
- `use_reference_images`: 是否使用用户消息中的图片作为参考（默认为 False，可在调用时显式开启）

#### 工具调用示例

```json
{
  "tool": "gemini-pic-gen",
  "arguments": {
    "image_description": "A blue cat sitting on a futuristic balcony at sunset",
    "use_reference_images": false
  }
}
```

当 `use_reference_images` 设为 `true` 且消息中附带图片或引用了历史图片时，插件会自动提取这些图片并作为生成参考；如果未附带图片或显式关闭该参数，则执行纯文本文生图。

#### 命令/自然语言兜底

如果通过命令或自然语言触发（未显式指定 `image_description` 参数），插件会尝试从消息文本中提取描述；若仍为空，会返回使用提示：“请在指令后添加描述，例如：文生图 一只蓝猫。”

### 智能重试机制

插件内置了双层重试机制，提高图像生成的成功率：

#### 重试策略
- **API密钥轮换**: 当一个API密钥失败时，自动切换到下一个可用密钥
- **单密钥重试**: 对每个API密钥都会进行用户配置次数的重试
- **智能错误分类**: 额度/速率限制错误直接切换密钥，网络/临时错误进行重试
- **指数退避**: 重试间隔2秒→4秒→8秒，最大10秒

#### 总重试次数计算
```
总重试次数 = API密钥数量 × max_retry_attempts
```

例如：3个API密钥，每个重试3次 = 最多9次尝试

### 使用场景

插件支持以下使用场景：
- **纯文本生成图像**: 直接通过文字描述生成图片
- **基于参考图片生成/修改**: 上传图片后，可以基于该图片进行修改或生成新图片
- **智能参考控制**: 插件会自动判断是否使用参考图片

### 常见问题与排查

- **提示“请在指令后添加描述”**：表示未收到有效的 `image_description`，请在命令或自然语言请求中补充图像描述。
- **日志定位**：调用会输出原始参数、当前使用的模型以及最大重试次数，便于在服务端快速定位路由或配置问题。

## 技术实现

### 核心组件

- **main.py**: 插件主要逻辑，继承自 AstrBot 的 Star 类
- **utils/ttp.py**: OpenRouter API 调用和图像处理逻辑
- **utils/file_send_server.py**: 文件传输服务器通信

### 工作流程

1. 接收用户的图像生成请求和可选的参考图片
2. 根据 `use_reference_images` 参数决定是否使用参考图片
3. 构建多模态请求消息（文本+图片）发送到 OpenRouter API
4. 调用 Gemini 2.5 Flash 模型进行图像生成或修改
5. 解析返回的 base64 图像数据
6. 自动清理超过15分钟的历史图像文件
7. 保存新生成的图像到本地文件系统
8. 通过文件传输服务发送图像（如需要）
9. 返回图像链到聊天

### 支持的模型

插件支持配置任何 OpenRouter 平台上可用的图像生成模型，包括但不限于：

- `google/gemini-2.5-flash-image-preview:free`（默认免费模型）
- `google/gemini-2.0-flash-exp:free`
- `openai/gpt-4o`
- `anthropic/claude-3.5-sonnet`
- 其他支持图像生成的 OpenRouter 模型

您可以在插件的配置文件中的 `model_name` 字段指定要使用的模型。

## 文件结构

```
AstrBot_plugin_gemini2.5image-openrouter/
├── main.py                 # 插件主文件
├── metadata.yaml          # 插件元数据
├── _conf_schema.json      # 配置模式定义
├── utils/
│   ├── ttp.py            # OpenRouter API 调用
│   └── file_send_server.py # 文件传输工具
├── images/               # 生成的图像存储目录
├── LICENSE              # 许可证文件
└── README.md           # 项目说明文档
```

## 错误处理

插件包含完善的错误处理机制：

- **API 调用失败处理**: 详细的 OpenRouter API 错误信息记录
- **Base64 图像解码错误处理**: 自动检测和修复格式问题
- **参考图片处理异常捕获**: 当参考图片转换失败时的回退机制
- **文件传输异常捕获**: 网络传输失败时的错误提示
- **自动清理失败处理**: 清理历史文件时的异常保护
- **详细的错误日志输出**: 便于调试和问题定位

## 版本信息

- **当前版本**: v1.8.1
- **更新内容**:
  - 🔄 对齐 `metadata.yaml` 与处理函数签名，修复工具参数被忽略的问题
  - 🧭 增强参数兜底与日志记录，便于定位缺参或路由问题
  - 📘 更新文档，补充工具调用示例与常见问题排查

## 开发信息

- **作者**: 喵喵
- **版本**: v1.5
- **许可证**: 见 LICENSE 文件
- **项目地址**: [GitHub Repository](https://github.com/miaoxutao123/AstrBot_plugin_gemini2point5image-openrouter)

## 贡献

欢迎提交 Issue 和 Pull Request 来改进这个插件。

## 许可证

本项目采用开源许可证，详见 LICENSE 文件。