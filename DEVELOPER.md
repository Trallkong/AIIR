# 开发者文档 — AI UI Pipeline

## 架构概览

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│  index.html  │────▶│  backend_api.py  │────▶│  DeepSeek AI │
│  (VanillaJS) │     │  (FastAPI)       │     │  (OpenAI SDk)│
└─────────────┘     └──────────────────┘     └──────────────┘
                          │
                    ┌─────┴──────┐
                    │ psd-tools  │
                    └────────────┘
```

- 前端不依赖任何框架（纯 Vanilla JS）
- 后端用 FastAPI 同时 serve 前端静态文件和 API
- AI 通过 OpenAI SDK 调用 DeepSeek API
- PSD 解析用 `psd-tools` 库

## 关键文件

### `backend_api.py` — 核心服务

```
backend_api.py
├── 数据模型          BaseModel 定义
│   ├── UILayer               PSD 图层节点
│   ├── ComponentInfo         AI 分类后的组件信息
│   ├── GenerationResult      生成结果
│   ├── GenerateCodeRequest   生成请求体
│   └── ExportRequest         导出请求体
│
├── PSD 解析模块
│   └── parse_psd_layers()    用 psd-tools 读取二进制 PSD
│
├── AI 分类模块
│   └── classify_ui_components()  调用 DeepSeek 识别组件类型
│
├── 代码生成模块
│   ├── generate_unity_code()    Unity C# prompt + API 调用
│   ├── generate_cocos_code()    Cocos2dx JS prompt + API 调用
│   └── _strip_code_fence()      清理 AI 返回的 markdown 包裹
│
├── 质量校验模块
│   └── generate_validation_report()  检测命名/坐标/层级问题
│
└── API 路由
    ├── GET  /                   返回 index.html
    ├── POST /api/analyze-psd    上传 PSD，返回分类结果
    ├── POST /api/generate-code  生成引擎代码
    └── POST /api/export-project 导出 ZIP
```

### `index.html` — 前端

单页应用，所有逻辑内联在 `<script>` 中。

```
index.html
├── CSS (~400 行)    UI/UX Pro Max 设计系统
│   ├── 变量          --surface, --accent, --radius 等
│   ├── 布局          header / container / tabs / panels
│   ├── 组件          card / upload-area / code-block / toast
│   └── 响应式        移动端适配
│
├── HTML             三个面板：upload / components / code
│
└── JS               状态管理 + 事件处理
    ├── i18n          中英双语映射
    ├── 状态变量       currentComponents / currentLayerStructure
    ├── 核心流程       triggerAnalyze → generateCode → exportProject
    └── 工具函数       switchTab / showToast / copyCode
```

## API 契约

### POST /api/generate-code

```json
{
  "layer_structure": { "name": "...", "children": [...] },
  "project_name": "AirconditionPanel",
  "target_engines": ["unity", "cocos"],
  "custom_instructions": ""
}
```

- `target_engines` 缺省时默认 `["unity", "cocos"]`
- `custom_instructions` 追加到 prompt 末尾，不覆盖内部 prompt

### POST /api/export-project

```json
{
  "project_name": "AirconditionPanel",
  "unity_code": "...",
  "cocos_code": "...",
  "components": [...]
}
```

返回 `StreamingResponse` (ZIP)。

## 扩展指南

### 添加新引擎

1. 在 `backend_api.py` 新建生成函数，参考 `generate_cocos_code()` 的 prompt + API 调用模式
2. 在 `GenerateCodeRequest.target_engines` 中添加新引擎标识
3. 在 `generate_code()` 路由中添加条件分支
4. 前端 `engine-toggles` 中添加对应的 checkbox
5. 在 `getTargetEngines()` 和 `updateExportHint()` 中添加映射

### 修改 Prompt 策略

内部 prompt 分两段：
1. **上下文 + 组件数据** — 代码中的固定字符串
2. **用户自定义指令** — 来自 `custom_instructions`，拼接在末尾

修改内置 prompt 时注意保持 f-string 中的 `{components_json}` 和 `{project_name}` 占位符。

### 前端 UI 规范

- 所有文本必须走 `data-i18n` + `t()` 机制，不要硬编码
- 使用 CSS 变量（`var(--surface)` 等）保证暗色主题一致
- `showToast()` 作为全局反馈组件，不要用 `alert()`
- 避免 `querySelector(':first-of-type')` 等位置相关选择器——`#code` 面板结构变动会失效，改用 `querySelectorAll()` + 下标

## 已知限制

- `classify_ui_components()` 和代码生成函数中的 `OpenAI` 调用是同步的，会阻塞 asyncio 事件循环。生产环境建议用 `asyncio.to_thread()` 或独立线程池
- 没有 HTTP 超时设置，DeepSeek API 挂起时请求会一直等待
- Unity 代码中的 TMPro 字体路径 `Resources.Load<TMP_FontAsset>("Fonts & Materials/LiberationSans SDF")` 是 Unity 内置路径，不同项目可能需要调整
- Cocos2dx 代码使用 `cc.Class()` 语法（Cocos2dx-JS v3），不兼容 Cocos Creator
