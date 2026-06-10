"""
AIUIPipeline 后端 API - FastAPI 服务
负责：PSD解析、AI分类、多引擎代码生成
使用DeepSeek API替代Claude
"""
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import base64
import io
import zipfile
from datetime import datetime
from openai import OpenAI
import os

app = FastAPI(title="AIUIPipeline", version="1.0")

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 提供前端页面
@app.get("/")
async def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ==================== 数据模型 ====================

class UILayer(BaseModel):
    name: str
    type: str  # group/text/image/shape
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    visible: bool = True
    opacity: int = 100
    children: Optional[List['UILayer']] = None
    properties: Dict[str, Any] = {}

UILayer.model_rebuild()

class ComponentInfo(BaseModel):
    name: str
    layerType: str
    semanticType: str  # button/text/image/list/panel/slider等
    confidence: float
    position: Dict[str, int]  # {x, y}
    size: Dict[str, int]  # {width, height}
    properties: Dict[str, Any]

class GenerationResult(BaseModel):
    projectName: str
    unityCode: str
    cocosCode: str
    components: List[ComponentInfo]
    validation: Dict[str, Any]

class GenerateCodeRequest(BaseModel):
    layer_structure: Dict[str, Any]
    project_name: str = "UIPanel"
    target_engines: Optional[List[str]] = None
    custom_instructions: str = ""

class ExportRequest(BaseModel):
    project_name: str
    unity_code: str
    cocos_code: str
    components: List[Dict[str, Any]]

# ==================== PSD 解析模块 ====================

def _rgba_to_hex(rgba: list) -> str:
    """Convert RGBA values (0-255) to hex string."""
    if len(rgba) >= 3:
        return "#{:02X}{:02X}{:02X}".format(
            int(rgba[0]), int(rgba[1]), int(rgba[2])
        )
    return "#000000"


def parse_psd_layers(psd_binary: bytes) -> Dict[str, Any]:
    """
    使用 psd-tools 解析 PSD 二进制数据，返回标准化图层结构。
    """
    from psd_tools import PSDImage
    from psd_tools.api.layers import TypeLayer

    psd = PSDImage.open(io.BytesIO(psd_binary))

    def extract_properties(layer) -> Dict[str, Any]:
        props = {}
        if layer.kind == "type":
            try:
                props["content"] = layer.text or ""
                ed = layer.engine_dict or {}
                style = ed.get("style", {}) or {}
                font_set = style.get("fontSet", []) or []
                if font_set:
                    props["fontSize"] = font_set[0].get("fontSize", 0)
                    fill = font_set[0].get("fillColor", {}).get("values", [0, 0, 0, 255])
                    props["fontColor"] = _rgba_to_hex(fill)
            except Exception:
                pass
        return props

    def layer_type(layer) -> str:
        kind = layer.kind
        if kind in ("group", "artboard"):
            return "group"
        if kind == "type":
            return "text"
        if kind == "shape":
            return "shape"
        return "image"

    def convert_layer(layer):
        x, y = layer.offset
        w, h = layer.size
        node = {
            "name": layer.name or "Unnamed",
            "type": layer_type(layer),
            "x": int(x),
            "y": int(y),
            "width": int(w),
            "height": int(h),
            "visible": layer.visible,
            "opacity": int(layer.opacity),
            "properties": extract_properties(layer),
        }
        children = []
        if layer.is_group():
            for child in layer:
                children.append(convert_layer(child))
        if children:
            node["children"] = children
        return node

    root = {
        "name": psd.name or "Untitled",
        "type": "group",
        "width": psd.width,
        "height": psd.height,
        "children": [convert_layer(layer) for layer in psd],
    }
    return root

# ==================== AI 分类模块 ====================

def classify_ui_components(layer_structure: Dict[str, Any]) -> List[ComponentInfo]:
    """
    使用DeepSeek AI识别UI组件类型
    """
    # 初始化DeepSeek客户端
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
    
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    
    # 构建提示词 - 这是关键的AI部分
    component_list = json.dumps(layer_structure, ensure_ascii=False, indent=2)
    
    prompt = f"""你是游戏UI专家。请分析以下PSD图层结构，识别每个组件的UI类型。

PSD图层结构：
{component_list}

请为每个图层识别其语义UI类型（这是最关键的部分）。分类规则：
1. Button (按钮)：通常有背景色、边框、交互感 - 用于点击操作
2. Text (纯文本)：只有文字内容，无背景或装饰
3. Input (输入框)：有边框、背景，用于用户输入
4. Image (图片)：纯图像组件，不是交互元素
5. Panel/Container (面板)：分组容器，包含其他元素
6. Slider (滑块)：用于范围选择
7. Toggle (开关)：用于状态切换
8. Label (标签)：带有描述文字

请返回JSON格式：
{{
  "components": [
    {{
      "name": "组件名",
      "semanticType": "组件语义类型",
      "confidence": 0.95,
      "reason": "识别理由"
    }}
  ]
}}"""

    message = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    # 解析AI返回结果
    response_text = message.choices[0].message.content
    
    # 尝试从响应中提取JSON
    try:
        # 查找JSON对象
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}') + 1
        if start_idx >= 0 and end_idx > start_idx:
            json_str = response_text[start_idx:end_idx]
            ai_result = json.loads(json_str)
            components_data = ai_result.get("components", [])
        else:
            components_data = []
    except json.JSONDecodeError:
        components_data = []
    
    # 映射到组件信息
    components = []
    
    def process_layer(layer: Dict, parent_x: int = 0, parent_y: int = 0):
        x = parent_x + layer.get("x", 0)
        y = parent_y + layer.get("y", 0)
        
        # 从AI结果中查找该层的分类
        ai_type = "container"
        confidence = 0.7
        for ai_comp in components_data:
            if ai_comp.get("name") == layer["name"]:
                ai_type = ai_comp.get("semanticType", "unknown")
                confidence = ai_comp.get("confidence", 0.7)
                break
        
        # 根据层类型和名称的启发式规则调整
        if layer["type"] == "text" or layer["type"] == "group" and "text" in layer["name"].lower():
            if any(word in layer["name"].lower() for word in ["button", "btn", "click"]):
                ai_type = "button"
            elif any(word in layer["name"].lower() for word in ["input", "password", "username"]):
                ai_type = "input"
            else:
                ai_type = "text"
        
        comp = ComponentInfo(
            name=layer["name"],
            layerType=layer["type"],
            semanticType=ai_type,
            confidence=confidence,
            position={"x": x, "y": y},
            size={"width": layer.get("width", 0), "height": layer.get("height", 0)},
            properties=layer.get("properties", {})
        )
        components.append(comp)
        
        # 递归处理子层
        for child in layer.get("children", []):
            process_layer(child, x, y)
    
    for layer in layer_structure.get("children", []):
        process_layer(layer)
    
    return components

# ==================== 代码生成模块 ====================

def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences (```...) from AI response."""
    import re
    text = re.sub(r'^```\w*\s*\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n```\s*$', '', text)
    return text.strip()


def generate_unity_code(components: List[ComponentInfo], project_name: str, custom_instructions: str = "") -> str:
    """
    生成Unity C# UGUI代码
    """
    # 初始化DeepSeek客户端
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
    
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    
    components_json = json.dumps([c.model_dump() for c in components], ensure_ascii=False, indent=2)
    
    prompt = f"""作为Unity游戏开发专家，请根据以下UI组件数据生成 **完整的C# UGUI代码**。

项目名称：{project_name}

UI组件数据（JSON数组，每个元素包含 name, layerType, semanticType, position:{{x,y}}, size:{{width,height}}, properties:{{content,fontSize,fontColor,...}}）：
{components_json}

你必须严格遵循以下要求：

1. **禁止硬编码组件数据** — 不要将以上 UI组件数据 硬编码到代码中。假设调用方在运行时通过 `SetComponentData(List<UIComponentData> data)` 方法将数据注入，代码中只声明 `List<UIComponentData>` 字段和 Setter 方法
2. **遍历组件数据** — 在 InitializeComponents() 中遍历 `this.components` 列表，为每个组件调用创建方法，使用 `name`、`position.x/y`、`size.width/height`、`properties.content`、`properties.fontSize` 等属性
3. **坐标系转换** — PSD坐标(0,0左上角) → Canvas坐标(0.5,0.5锚点)，使用 ConvertPSDToCanvas(psdX, psdY, canvasWidth, canvasHeight)
4. **完整的 MonoBehaviour 生命周期** — Awake()调用 OnInitialize()，Start()留空
5. **使用 TextMeshPro**（TMPro）替代旧的 UnityEngine.UI.Text，字体用 Resources.Load&lt;TMP_FontAsset&gt;("Fonts & Materials/LiberationSans SDF")
6. **每个按钮必须包含子 TextMeshProUGUI** 显示按钮文字（从 properties.content 读取）
7. **禁止模板/存根** — 不要写 "// 在此处添加" 或空方法体

代码要求：
- 命名空间 namespace UIGenerated
- 类名直接使用项目名（不要追加 Panel 后缀），例如 "LoginPanel" → 类名 LoginPanel
- 包含 OnInitialize() → InitializeComponents() → SetupLayout() → RegisterEventHandlers()
- 使用 RectTransform

请直接返回完整的C#代码，不要额外说明。"""

    if custom_instructions:
        prompt += f"\n\n用户额外要求：\n{custom_instructions}"

    message = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=8192,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    return _strip_code_fence(message.choices[0].message.content)

def generate_cocos_code(components: List[ComponentInfo], project_name: str, custom_instructions: str = "") -> str:
    """
    生成Cocos2dx JavaScript代码
    """
    # 初始化DeepSeek客户端
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 环境变量未设置")
    
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    
    components_json = json.dumps([c.model_dump() for c in components], ensure_ascii=False, indent=2)
    
    prompt = f"""作为Cocos2dx游戏开发专家，请根据以下UI组件数据生成 **完整的JavaScript代码**。

项目名称：{project_name}

UI组件数据（JSON数组，每个元素包含 name, layerType, semanticType, position:{{x,y}}, size:{{width,height}}, properties:{{content,fontSize,fontColor,...}}）：
{components_json}

你必须严格遵循以下要求：

1. **禁止硬编码组件数据** — 不要将以上 UI组件数据 硬编码到代码中。构造函数通过参数接收数据：`ctor: function(components) {{ this._super(); this.components = components || []; }}`
2. **遍历组件数据** — 在 initUI() 中遍历 `this.components`，为每个组件创建 cc.Node，使用 `name`、`position.x/y`、`size.width/height`、`properties.content`、`properties.fontSize`
3. **坐标系转换** — Cocos2dx (0,0左下角)，PSD (0,0左上角)：cocosX = psdX + size.width / 2, cocosY = winSize.height - psdY - size.height / 2（节点锚点默认 0.5,0.5，所以宽高各一半定位中心）
4. **使用现代 Cocos2dx API** — cc.Label 代替 cc.LabelTTF；创建空白 Sprite 用 `new cc.Sprite()`，不要传 cc.rect
5. **按钮使用 ccui.Button** 并设置标题文字（从 properties.content 读取）
6. **注册事件时对所有 ccui.Button 都绑定点击监听**，不要根据名称关键字判断
7. **默认字体颜色用白色** cc.color(255, 255, 255)，不要用黑色
8. **如果组件 semanticType 为 "list" 或 "scroll"，使用 cc.ScrollView**
9. **禁止模板/存根** — 每个方法必须具体实现

代码要求：
- ES6 class 语法：类名直接使用项目名（不要追加 Layer 后缀），例如 "LoginPanel" → 类名 LoginPanel，继承 cc.Layer
- onEnter() → initUI() → registerEvents()
- onExit() → cleanup()
- 每个UI元素通过 setName() 设置 name

请直接返回完整的JavaScript代码，不要额外说明。"""

    if custom_instructions:
        prompt += f"\n\n用户额外要求：\n{custom_instructions}"

    message = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=8192,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    return _strip_code_fence(message.choices[0].message.content)

def generate_validation_report(components: List[ComponentInfo]) -> Dict[str, Any]:
    """
    质量校验 - 检测潜在问题
    """
    issues = []
    
    # 检查命名规范
    for comp in components:
        if not comp.name[0].isupper():
            issues.append({
                "level": "warning",
                "type": "naming",
                "message": f"'{comp.name}' 应该以大写字母开头"
            })
    
    # 检查组件类型合理性
    for comp in components:
        if comp.semanticType == "unknown":
            issues.append({
                "level": "info",
                "type": "classification",
                "message": f"'{comp.name}' 无法自动分类，建议手动检查"
            })
    
    # 检查是否有重复名称
    names = [c.name for c in components]
    for name in set(names):
        if names.count(name) > 1:
            issues.append({
                "level": "error",
                "type": "duplicate",
                "message": f"发现重复的组件名: '{name}'"
            })
    
    # 检查坐标有效性
    for comp in components:
        if comp.size["width"] <= 0 or comp.size["height"] <= 0:
            issues.append({
                "level": "warning",
                "type": "size",
                "message": f"'{comp.name}' 的大小不合理"
            })
    
    return {
        "total_issues": len(issues),
        "errors": len([i for i in issues if i["level"] == "error"]),
        "warnings": len([i for i in issues if i["level"] == "warning"]),
        "issues": issues
    }

# ==================== API 端点 ====================

@app.post("/api/analyze-psd")
async def analyze_psd(file: UploadFile = File(...)):
    """
    上传PSD文件进行分析
    """
    try:
        content = await file.read()
        
        # PSD解析
        layer_structure = parse_psd_layers(content)
        
        # AI分类
        components = classify_ui_components(layer_structure)
        
        return {
            "status": "success",
            "layerStructure": layer_structure,
            "components": [c.model_dump() for c in components]
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/generate-code")
async def generate_code(body: GenerateCodeRequest):
    layer_structure = body.layer_structure
    project_name = body.project_name
    target_engines = body.target_engines or ["unity", "cocos"]
    custom_instructions = body.custom_instructions.strip()
    """
    生成多引擎代码
    """
    try:
        # 分类组件
        components = classify_ui_components(layer_structure)
        
        # 生成代码（按需生成）
        unity_code = generate_unity_code(components, project_name, custom_instructions) if "unity" in target_engines else ""
        cocos_code = generate_cocos_code(components, project_name, custom_instructions) if "cocos" in target_engines else ""
        
        # 质量检验
        validation = generate_validation_report(components)
        
        result = GenerationResult(
            projectName=project_name,
            unityCode=unity_code,
            cocosCode=cocos_code,
            components=components,
            validation=validation
        )
        
        return result.model_dump()
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/export-project")
async def export_project(body: ExportRequest):
    project_name = body.project_name
    unity_code = body.unity_code
    cocos_code = body.cocos_code
    components = body.components
    """
    导出完整项目包
    """
    try:
        # 创建ZIP包
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Unity代码
            zf.writestr(f"Unity/{project_name}Panel.cs", unity_code)
            zf.writestr("Unity/README.md", 
                f"# Unity UI Panel\n\n## {project_name}Panel\n\n将生成的C#代码导入到Assets/Scripts/UI目录")
            
            # Cocos2dx代码
            zf.writestr(f"Cocos2dx/{project_name}Layer.js", cocos_code)
            zf.writestr("Cocos2dx/README.md",
                f"# Cocos2dx UI Layer\n\n## {project_name}Layer\n\n将生成的JS代码导入到src/scenes目录")
            
            # 组件配置
            components_config = {
                "projectName": project_name,
                "components": components,
                "generated": datetime.now().isoformat(),
                "version": "1.0"
            }
            zf.writestr("components.json", json.dumps(components_config, ensure_ascii=False, indent=2))
            
            # 项目信息
            zf.writestr("PROJECT.json", json.dumps({
                "name": project_name,
                "type": "UIPanel",
                "engines": ["Unity", "Cocos2dx"],
                "created": datetime.now().isoformat()
            }, ensure_ascii=False, indent=2))
        
        zip_buffer.seek(0)
        return StreamingResponse(
            io.BytesIO(zip_buffer.getvalue()),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{project_name}_UIGen.zip"'}
        )
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": "1.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
